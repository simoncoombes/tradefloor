//! Vectorised engines: one call advances N independent markets.
//!
//! # What this is for, and what it is not for
//!
//! It is for vector environments and sweeps, the shape Gymnasium's vector
//! envs consume, and the shape a hyperparameter sweep wants.
//!
//! It is NOT primarily a boundary-cost optimisation, and the distinction
//! matters because the design once assumed otherwise. Measured on this crate,
//! a boundary crossing costs 0.357 µs against 249 µs of engine work per tick
//! at a hundred instruments, or 0.14%. Batching sixty-four engines into one call
//! saves sixty-three crossings and roughly twenty microseconds, against
//! sixteen milliseconds of actual simulation. The saving is real and it is
//! rounding error.
//!
//! What batching genuinely buys is a single columnar result across N markets,
//! which is what a vectorised policy wants to consume, and one place to put
//! the loop if this ever runs on more than one thread.
//!
//! # Per seed is the only safe boundary
//!
//! Each engine owns its own generators, so members cannot interact. The
//! stream split gave a run seven per-domain substreams (market, economy,
//! external, jumps, volume, news, volume_idio), but WITHIN each stream the
//! schedule is still strictly sequential, because every company settling on
//! a tick draws from the market stream in roster order with the Box-Muller
//! spare cached on it, so there is still no decomposition within a run that
//! preserves the draw schedule.
//!
//! It follows that whether the batch advances its members on one thread or
//! eight is unobservable in the output. That is the only parallelism this
//! library will ever offer, because it is the only one that cannot change a
//! market.

#![allow(unexpected_cfgs, clippy::useless_conversion)]

use pyo3::prelude::*;
use pyo3::types::PyBytes;

use crate::economy::create_initial_central_bank_state;
use crate::engine::{Engine, SessionBuffer, SessionRequest, TickRequest};
use crate::market::{GameTime, TickCompany};
use crate::python::ValidationError;

fn f64_bytes(py: Python<'_>, values: &[f64]) -> Py<PyBytes> {
    let mut out = Vec::with_capacity(values.len() * 8);
    for v in values {
        out.extend_from_slice(&v.to_le_bytes());
    }
    PyBytes::new_bound(py, &out).into()
}

/// N independent markets, advanced together.
#[pyclass(name = "EngineBatch", module = "pretium._core")]
pub struct PyEngineBatch {
    engines: Vec<Engine>,
    buffers: Vec<SessionBuffer>,
    seeds: Vec<u32>,
    tickers: Vec<String>,
    /// Whether `open_market` has been called and a day is in progress.
    ///
    /// Mirrors `Engine`, and it has to. Without it `run_session` re-opened on
    /// every call, so a day made of several sessions was several days here and
    /// one day there -- and a batch member stopped being the same market as a
    /// standalone `Engine` on the same seed. Measured before fixing: up to
    /// **0.5%** apart on prices after four sessions in a day.
    ///
    /// A batch has no `close_market`, so `open_market` is the only day
    /// boundary. That is the same contract `Engine` offers; `close_market`
    /// merely ends the day early there.
    market_open: bool,
}

#[pymethods]
impl PyEngineBatch {
    /// Build one engine per seed, all over the same universe.
    ///
    /// Seeds must be distinct. Two members with the same seed would be the
    /// same market twice, which is almost always a mistake in a sweep and is
    /// silent if allowed: the results look like two samples and are one.
    ///
    /// `model` selects the coefficient set, exactly as on `Engine`: one
    /// model for every member, because a batch is N seeds of the same
    /// market and members under different models would be a comparison of
    /// models presented as a comparison of seeds.
    #[new]
    #[pyo3(signature = (*, seeds, universe, macro_state = None, model = None))]
    fn new(
        seeds: Vec<u32>,
        universe: Vec<crate::python_engine::PyInstrument>,
        macro_state: Option<crate::python_engine::PyMacro>,
        model: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<Self> {
        if seeds.is_empty() {
            return Err(ValidationError::new_err("no seeds given"));
        }
        if universe.is_empty() {
            return Err(ValidationError::new_err("universe is empty"));
        }
        let mut sorted = seeds.clone();
        sorted.sort_unstable();
        sorted.dedup();
        if sorted.len() != seeds.len() {
            return Err(ValidationError::new_err(
                "seeds must be distinct - a repeated seed is the same market twice, \
                 which reads as two samples and is one",
            ));
        }

        let params = crate::python_engine::model_params_from(model)?;
        let economy = crate::python_engine::economy_from(macro_state)?;
        let companies: Vec<TickCompany> = universe
            .iter()
            .enumerate()
            .map(|(i, inst)| inst.to_core_public(i))
            .collect();
        let sector_keys: Vec<String> =
            crate::sectors::keys().iter().map(|s| s.to_string()).collect();

        let engines = seeds
            .iter()
            .map(|seed| {
                Engine::with_params(
                    *seed,
                    companies.clone(),
                    economy.clone(),
                    create_initial_central_bank_state(0),
                    sector_keys.clone(),
                    params.clone(),
                )
            })
            .collect::<Vec<_>>();
        let buffers = (0..engines.len()).map(|_| SessionBuffer::new()).collect();

        Ok(Self {
            engines,
            buffers,
            seeds,
            tickers: universe.iter().map(|i| i.ticker.clone()).collect(),
            market_open: false,
        })
    }

    fn open_market(&mut self) {
        self.market_open = true;
        for engine in &mut self.engines {
            engine.open_market();
        }
    }

    /// Advance every member one minute.
    #[pyo3(signature = (hour, minute, day_of_week, *, volatility = 1.0))]
    fn tick(
        &mut self,
        hour: i64,
        minute: i64,
        day_of_week: i64,
        volatility: f64,
    ) -> PyResult<()> {
        if !(0..24).contains(&hour) || !(0..60).contains(&minute) || !(0..7).contains(&day_of_week)
        {
            return Err(ValidationError::new_err("invalid time"));
        }
        for engine in &mut self.engines {
            engine.tick(&TickRequest {
                time: GameTime {
                    hour,
                    minute,
                    day_of_week,
                },
                volatility_multiplier: volatility,
                news: &[],
                news_impact_queue: &[],
                order_volumes: &[],
            });
        }
        Ok(())
    }

    /// Advance every member by `ticks` minutes.
    #[pyo3(signature = (hour, minute, day_of_week, ticks, *, volatility = 1.0))]
    fn run_session(
        &mut self,
        hour: i64,
        minute: i64,
        day_of_week: i64,
        ticks: usize,
        volatility: f64,
    ) -> PyResult<()> {
        if ticks == 0 {
            return Err(ValidationError::new_err("ticks must be greater than zero"));
        }
        // Open the day if the caller has not, and exactly once however many
        // sessions it holds. Same rule as `Engine`, so a batch member and a
        // standalone engine on the same seed stay the same market.
        if !self.market_open {
            self.open_market();
        }
        for (engine, buffer) in self.engines.iter_mut().zip(self.buffers.iter_mut()) {
            let n = engine.len();
            let innovations: Vec<Option<f64>> = vec![None; n];
            let variances: Vec<f64> = engine
                .companies()
                .iter()
                .map(|c| {
                    crate::sectors::by_key(&c.sector)
                        .map(|s| s.base_daily_variance())
                        .unwrap_or(0.000225)
                })
                .collect();
            engine.run_session(
                &SessionRequest {
                    start: GameTime {
                        hour,
                        minute,
                        day_of_week,
                    },
                    ticks,
                    volatility_multiplier: volatility,
                    news: &[],
                    news_impact_queue: &[],
                    order_volumes: &[],
                    close_at_end: false,
                    // False, matching `Engine`. The day is opened once, above.
                    //
                    // This was `true` on the argument that a batch has no day
                    // boundary. It does: `open_market`. Leaving it true made a
                    // batch member diverge from a standalone `Engine` on the
                    // same seed as soon as a day held more than one session --
                    // measured at up to 0.5% on prices after four. A fast path
                    // that is a different market is not a fast path.
                    reopen: false,
                    daily_innovations: &innovations,
                    sector_base_variances: &variances,
                    stop: None,
                },
                buffer,
            );
        }
        Ok(())
    }

    /// Prices across every member: `len(seeds) x len(tickers)`, row-major.
    ///
    /// Row-major with one member per row, so a vectorised policy reads one
    /// market's cross-section contiguously, which is the direction it
    /// actually consumes.
    fn prices(&self, py: Python<'_>) -> Py<PyBytes> {
        let mut out = Vec::with_capacity(self.engines.len() * self.tickers.len());
        for engine in &self.engines {
            out.extend_from_slice(&engine.prices());
        }
        f64_bytes(py, &out)
    }

    /// One column across every member, same shape as `prices`.
    fn column(&self, py: Python<'_>, field: &str) -> PyResult<Py<PyBytes>> {
        let f = crate::python_engine::parse_field_public(field)?;
        let mut out = Vec::with_capacity(self.engines.len() * self.tickers.len());
        for engine in &self.engines {
            out.extend_from_slice(&engine.column(f));
        }
        Ok(f64_bytes(py, &out))
    }

    /// Draws consumed by each member, in seed order.
    ///
    /// Two members should NOT agree here in general -- different seeds
    /// consume differently -- so this is a diagnostic for comparing a batch
    /// member against the same seed run alone.
    #[getter]
    fn draws_consumed(&self) -> Vec<usize> {
        self.engines.iter().map(|e| e.draws_consumed()).collect()
    }

    /// The honest name of the model every member runs: a shipped preset's
    /// name, or `custom-XXXXXXXX`. One value rather than one per member, since the
    /// constructor builds every engine from the same coefficient set.
    #[getter]
    fn model_fingerprint(&self) -> String {
        self.engines[0].params().fingerprint()
    }

    /// The model every member runs, as a `ModelParams`.
    #[getter]
    fn model(&self) -> crate::python_params::PyModelParams {
        crate::python_params::PyModelParams {
            inner: self.engines[0].params().clone(),
        }
    }

    #[getter]
    fn seeds(&self) -> Vec<u32> {
        self.seeds.clone()
    }

    #[getter]
    fn tickers(&self) -> Vec<String> {
        self.tickers.clone()
    }

    /// Number of markets in the batch.
    fn __len__(&self) -> usize {
        self.engines.len()
    }

    #[getter]
    fn shape(&self) -> (usize, usize) {
        (self.engines.len(), self.tickers.len())
    }

    fn __repr__(&self) -> String {
        format!(
            "EngineBatch({} markets x {} instruments)",
            self.engines.len(),
            self.tickers.len()
        )
    }
}
