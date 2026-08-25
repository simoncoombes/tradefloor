//! The run log: everything that crossed into the engine, in order.
//!
//! # Why a seed is not enough
//!
//! "Same seed, same market" holds only when nothing else varies. But the
//! market an agent trades in depends on the agent's own orders — they apply
//! pressure and move the price — so two runs with one seed and different order
//! flow are different markets, correctly.
//!
//! A seed therefore reproduces a run only if you also reproduce every input:
//! the sessions, the news, the flow, the macro pins, the roster edits, and any
//! draws the embedder took from the external stream. That whole sequence is
//! the specification of a run, and this is it.
//!
//! # What it is for
//!
//! Three things a seed alone cannot do. Replaying someone else's result
//! without their code. Bisecting a run to find the step where it diverged.
//! And archiving a published experiment as data rather than as a script that
//! may not run next year.
//!
//! # What it deliberately does not record
//!
//! Anything the engine can recompute. Prices, attribution, draw counts and
//! book state are all consequences, and logging them would create a second
//! source of truth that could disagree with the first. The log holds INPUTS
//! only; the outputs are whatever replaying them produces.

#![allow(unexpected_cfgs, clippy::useless_conversion)]

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

/// One recorded call.
#[derive(Debug, Clone)]
pub enum LogEntry {
    OpenMarket,
    CloseMarket,
    Tick {
        hour: i64,
        minute: i64,
        day_of_week: i64,
        volatility: f64,
        news: Vec<(Option<String>, Option<String>, f64)>,
        flow: Vec<(String, f64, f64)>,
    },
    RunSession {
        hour: i64,
        minute: i64,
        day_of_week: i64,
        ticks: usize,
        volatility: f64,
        close_at_end: bool,
        news: Vec<(Option<String>, Option<String>, f64)>,
        flow: Vec<(String, f64, f64)>,
    },
    PinMacro {
        fields: Vec<(String, f64)>,
        cycle: Option<String>,
    },
    ListInstrument {
        ticker: String,
        sector: String,
        initial_price: f64,
        shares_outstanding: f64,
        eps: Option<f64>,
        book_value_per_share: Option<f64>,
        revenue_growth: Option<f64>,
        avg_volume: f64,
        beta: f64,
        short_interest: f64,
    },
    Delist {
        index: usize,
    },
    /// A draw the embedder took from the EXTERNAL stream.
    ///
    /// Recorded because it MOVES THAT STREAM. Since the stream split it can
    /// no longer move the market — that is the cutover guarantee — but the
    /// embedder's own randomness is still part of the run's history: a
    /// replay that skipped a recorded draw would hand the embedder different
    /// values afterwards, and any decision built on them would diverge from
    /// the run the log claims to reproduce.
    Draw {
        normal: bool,
    },
    Record {
        day: u32,
    },
}

fn news_to_py(py: Python<'_>, news: &[(Option<String>, Option<String>, f64)]) -> PyResult<PyObject> {
    let out = PyList::empty_bound(py);
    for (ticker, sector, impact) in news {
        let d = PyDict::new_bound(py);
        d.set_item("ticker", ticker)?;
        d.set_item("sector", sector)?;
        d.set_item("price_impact", impact)?;
        out.append(d)?;
    }
    Ok(out.into())
}

fn flow_to_py(py: Python<'_>, flow: &[(String, f64, f64)]) -> PyResult<PyObject> {
    let d = PyDict::new_bound(py);
    for (ticker, buy, sell) in flow {
        // A LIST, not a tuple. The log's whole purpose is to be archived and
        // replayed as data, and a tuple does not survive a JSON round trip --
        // it comes back a list, so `log == json.loads(json.dumps(log))` was
        // false and an archived experiment did not compare equal to the run
        // that produced it. Replay worked either way, which is exactly why
        // this would have gone unnoticed until someone diffed two logs.
        d.set_item(ticker, PyList::new_bound(py, [*buy, *sell]))?;
    }
    Ok(d.into())
}

impl LogEntry {
    /// Render as a plain dict, so a log is JSON-serialisable without this
    /// crate taking an opinion about how it should be stored.
    pub fn to_py(&self, py: Python<'_>) -> PyResult<PyObject> {
        let d = PyDict::new_bound(py);
        match self {
            LogEntry::OpenMarket => {
                d.set_item("op", "open_market")?;
            }
            LogEntry::CloseMarket => {
                d.set_item("op", "close_market")?;
            }
            LogEntry::Tick {
                hour,
                minute,
                day_of_week,
                volatility,
                news,
                flow,
            } => {
                d.set_item("op", "tick")?;
                d.set_item("hour", hour)?;
                d.set_item("minute", minute)?;
                d.set_item("day_of_week", day_of_week)?;
                d.set_item("volatility", volatility)?;
                d.set_item("news", news_to_py(py, news)?)?;
                d.set_item("order_flow", flow_to_py(py, flow)?)?;
            }
            LogEntry::RunSession {
                hour,
                minute,
                day_of_week,
                ticks,
                volatility,
                close_at_end,
                news,
                flow,
            } => {
                d.set_item("op", "run_session")?;
                d.set_item("hour", hour)?;
                d.set_item("minute", minute)?;
                d.set_item("day_of_week", day_of_week)?;
                d.set_item("ticks", ticks)?;
                d.set_item("volatility", volatility)?;
                d.set_item("close_at_end", close_at_end)?;
                d.set_item("news", news_to_py(py, news)?)?;
                d.set_item("order_flow", flow_to_py(py, flow)?)?;
            }
            LogEntry::PinMacro { fields, cycle } => {
                d.set_item("op", "pin_macro")?;
                let f = PyDict::new_bound(py);
                for (name, value) in fields {
                    f.set_item(name, value)?;
                }
                if let Some(c) = cycle {
                    f.set_item("cycle", c)?;
                }
                d.set_item("fields", f)?;
            }
            LogEntry::ListInstrument {
                ticker,
                sector,
                initial_price,
                shares_outstanding,
                eps,
                book_value_per_share,
                revenue_growth,
                avg_volume,
                beta,
                short_interest,
            } => {
                d.set_item("op", "list_instrument")?;
                d.set_item("ticker", ticker)?;
                d.set_item("sector", sector)?;
                d.set_item("initial_price", initial_price)?;
                d.set_item("shares_outstanding", shares_outstanding)?;
                d.set_item("eps", eps)?;
                d.set_item("book_value_per_share", book_value_per_share)?;
                d.set_item("revenue_growth", revenue_growth)?;
                d.set_item("avg_volume", avg_volume)?;
                d.set_item("beta", beta)?;
                d.set_item("short_interest", short_interest)?;
            }
            LogEntry::Delist { index } => {
                d.set_item("op", "delist")?;
                d.set_item("index", index)?;
            }
            LogEntry::Draw { normal } => {
                d.set_item("op", if *normal { "draw_normal" } else { "draw_uniform" })?;
            }
            LogEntry::Record { day } => {
                d.set_item("op", "record")?;
                d.set_item("day", day)?;
            }
        }
        Ok(d.into())
    }
}
