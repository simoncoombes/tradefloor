//! The sector table.
//!
//! # Why this crate owns it now
//!
//! `fair_value.rs` takes `sector_avg_pe` as an already-looked-up number, with
//! a comment explaining that the crate deliberately does not own the sector
//! table because "the reference implementation already owns it" and a copy would be a
//! second source of truth.
//!
//! That reasoning was right and has now expired. Once this crate IS the
//! published library there is no reference implementation alongside it, and a Python user
//! passes `sector="technology"`, not an anchor P/E they looked up themselves.
//! Requiring them to supply the multiple would export an internal detail and
//! guarantee that no two users' "technology" meant the same thing.
//!
//! So the table lives here, and the second-source-of-truth risk is answered
//! by a test rather than by not having the data: [`tests::anchors_match_the_
//! reference_goldens`] checks every anchor against `meta.sectorAnchors` in
//! `goldens/fairvalue.json`, which is generated from the reference
//! implementation and is the parity contract.
//!
//! # Order is contractual
//!
//! Sectors iterate in the declared order below — `technology` through
//! `transportation` — because draw schedules depend on it. This is the order
//! in the reference implementation's `SECTOR_CONFIGS` literal. A `HashMap` here would be a
//! correctness bug, not a style choice, which is why it is a fixed array.

/// One sector's model parameters.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Sector {
    /// Stable key. This is what crosses the API boundary.
    pub key: &'static str,
    /// Human-facing name. Presentation only; never keyed on.
    pub display_name: &'static str,
    /// Anchor P/E, before rate and QE adjustment.
    pub avg_pe: f64,
    /// Spread volatility multiplier — `0.7 + 0.3 * volatility * beta`.
    ///
    /// DIMENSIONLESS and relative (0.6 to 1.3). This is not a volatility in
    /// any unit, and deriving a variance from it is a mistake the reference
    /// implementation actually made and had to fix: call sites once used
    /// `(volatility / sqrt(252))^2` for the GARCH bounds, which is a different
    /// quantity entirely. Use [`Sector::base_daily_variance`] for that.
    pub volatility: f64,
    /// Long-run DAILY return standard deviation, as a fraction.
    ///
    /// The real dispersion measure: technology 2.5%/day, utilities 0.8%/day.
    /// Squared, it is the long-run variance the GARCH floor and ceiling scale
    /// from, and what a fresh company seeds `garch_variance` with.
    pub daily_sigma: f64,
    /// The probability this sector is drawn as the EPICENTRE of a crisis
    /// episode. The weights sum to at most one and the remainder is `none`,
    /// a crisis with no epicentre.
    ///
    /// DERIVED from five crisis episodes on the tape
    /// (`results/ptv19refine/epicentre-derivation.json` in the design
    /// repository): per-sector volatility in each episode over each name's
    /// own calm-day volatility, median over the 39 real names of the
    /// roster, each sector taken relative to the median sector of that
    /// episode. The rule, stated before the numbers were read: the
    /// epicentre is the sector furthest above the episode's median if it is
    /// 1.3x or more above it. 2008-09 gives financial services at 2.43,
    /// 2011 financial services at 1.93, 2020 financial services at 1.41;
    /// 2000-02 and 2022 have no sector that far out. Three of five, all
    /// three the same sector, so `financial_services` carries 0.6 and
    /// `none` the remaining 0.4.
    ///
    /// The other eleven carry 0.0, and the four with no name on the tape --
    /// materials, real estate, utilities, transportation -- carry it as
    /// UNDETERMINED rather than as a measurement. Five episodes is the
    /// whole record and it says so; a sixth episode centred on energy
    /// would move these numbers and nothing here pretends otherwise.
    ///
    /// Read only when [`crate::params::ModelParams::crisis_epicentre_extra`]
    /// is non-zero, which no shipped preset sets.
    pub crisis_weight: f64,
}

impl Sector {
    /// Long-run daily variance — `daily_sigma^2`.
    ///
    /// The GARCH floor is 0.25x this and the ceiling 5x, so taking it from the
    /// wrong field rescales every company's volatility bounds without
    /// producing an obviously wrong number anywhere.
    pub fn base_daily_variance(&self) -> f64 {
        self.daily_sigma * self.daily_sigma
    }
}

/// The twelve sectors, in contractual declaration order.
pub const SECTORS: [Sector; 12] = [
    Sector { key: "technology",             display_name: "Technology",             avg_pe: 32.0, volatility: 1.2 , daily_sigma: 0.025, crisis_weight: 0.0 },
    Sector { key: "financial_services",     display_name: "Financial Services",     avg_pe: 12.0, volatility: 1.1 , daily_sigma: 0.015, crisis_weight: 0.6 },
    Sector { key: "healthcare",             display_name: "Healthcare",             avg_pe: 24.0, volatility: 0.9 , daily_sigma: 0.018, crisis_weight: 0.0 },
    Sector { key: "energy",                 display_name: "Energy",                 avg_pe: 10.0, volatility: 1.3 , daily_sigma: 0.015, crisis_weight: 0.0 },
    Sector { key: "consumer_discretionary", display_name: "Consumer Discretionary", avg_pe: 20.0, volatility: 1.0 , daily_sigma: 0.018, crisis_weight: 0.0 },
    Sector { key: "consumer_staples",       display_name: "Consumer Staples",       avg_pe: 20.0, volatility: 0.7 , daily_sigma: 0.008, crisis_weight: 0.0 },
    Sector { key: "industrials",            display_name: "Industrials",            avg_pe: 17.0, volatility: 1.0 , daily_sigma: 0.015, crisis_weight: 0.0 },
    Sector { key: "materials",              display_name: "Materials",              avg_pe: 14.0, volatility: 1.2 , daily_sigma: 0.015, crisis_weight: 0.0 },
    Sector { key: "real_estate",            display_name: "Real Estate",            avg_pe: 35.0, volatility: 0.9 , daily_sigma: 0.008, crisis_weight: 0.0 },
    Sector { key: "utilities",              display_name: "Utilities",              avg_pe: 16.0, volatility: 0.6 , daily_sigma: 0.008, crisis_weight: 0.0 },
    Sector { key: "telecommunications",     display_name: "Telecommunications",     avg_pe: 14.0, volatility: 0.8 , daily_sigma: 0.01, crisis_weight: 0.0 },
    Sector { key: "transportation",         display_name: "Transportation",         avg_pe: 15.0, volatility: 1.1 , daily_sigma: 0.015, crisis_weight: 0.0 },
];

/// Look up a sector by key.
///
/// Returns `None` for an unknown key rather than falling back to a default.
/// The valuation path already has a `|| 18` default for a MISSING anchor, and
/// silently routing a typo into it would value `"tecnology"` at the generic
/// multiple and report nothing — a wrong number that looks like a right one.
pub fn by_key(key: &str) -> Option<&'static Sector> {
    SECTORS.iter().find(|s| s.key == key)
}

/// The `none` weight: one minus the sum of the table's, floored at zero.
///
/// `none` is a crisis with no epicentre, which is two of the tape's five
/// episodes and is therefore a DRAW rather than the absence of one. It has
/// no row in the table because it is not a sector; it is the remainder, so
/// the table cannot be edited into a set of weights that does not sum to
/// one.
pub fn crisis_none_weight() -> f64 {
    let sum: f64 = SECTORS.iter().map(|s| s.crisis_weight).sum();
    if sum >= 1.0 {
        0.0
    } else {
        1.0 - sum
    }
}

/// Draw an epicentre from the table's weights given one uniform in [0, 1).
///
/// Returns the index of the drawn sector, or `None` for the remainder --
/// `none`, a crisis with no epicentre. Walks the table in declaration order,
/// which is the same order every other draw schedule reads it in, so the
/// mapping from a uniform to a sector is fixed by the table rather than by
/// a hash iteration.
pub fn draw_crisis_epicentre(u: f64) -> Option<usize> {
    let mut acc = 0.0;
    for (i, s) in SECTORS.iter().enumerate() {
        acc += s.crisis_weight;
        if u < acc {
            return Some(i);
        }
    }
    None
}

/// Every sector key, in declaration order.
pub fn keys() -> [&'static str; 12] {
    let mut out = [""; 12];
    let mut i = 0;
    while i < SECTORS.len() {
        out[i] = SECTORS[i].key;
        i += 1;
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn goldens() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("goldens")
    }

    #[test]
    fn anchors_match_the_reference_goldens() {
        // The answer to "isn't this a second source of truth?" -- it is, and
        // this is the gate that keeps the two identical. meta.sectorAnchors is
        // generated from the reference implementation and is the parity contract.
        // Skip rather than fail when the corpus is absent. The goldens are
        // 135 MB and are excluded from the published crate, so a consumer
        // running `cargo test` would otherwise see a failure that says
        // nothing about the code. In the repository, where the corpus is
        // present, this still runs and still gates.
        let path = goldens().join("fairvalue.json");
        if !path.exists() {
            eprintln!("skipping: {} absent (excluded from the published crate)",
                      path.display());
            return;
        }
        let raw = std::fs::read_to_string(&path)
            .expect("fairvalue.json present but unreadable");
        let json: serde_json::Value = serde_json::from_str(&raw).unwrap();
        let anchors = json["meta"]["sectorAnchors"]
            .as_object()
            .expect("meta.sectorAnchors missing");

        assert_eq!(
            anchors.len(),
            SECTORS.len(),
            "golden pins {} sectors, table has {}",
            anchors.len(),
            SECTORS.len()
        );

        for s in SECTORS.iter() {
            let want = anchors
                .get(s.key)
                .unwrap_or_else(|| panic!("golden has no anchor for {}", s.key))
                .as_f64()
                .unwrap();
            assert_eq!(s.avg_pe, want, "anchor P/E for {}", s.key);
        }
    }

    #[test]
    fn keys_are_unique_and_in_declaration_order() {
        // Order is contractual: draw schedules depend on sector iteration
        // order, so a reordering is a market change, not a refactor.
        let ks = keys();
        assert_eq!(ks[0], "technology");
        assert_eq!(ks[11], "transportation");
        let set: std::collections::HashSet<_> = ks.iter().collect();
        assert_eq!(set.len(), 12);
    }

    #[test]
    fn an_unknown_sector_is_none_not_a_default() {
        // A typo must not silently become the generic P/E of 18.
        assert!(by_key("tecnology").is_none());
        assert!(by_key("").is_none());
        assert_eq!(by_key("technology").unwrap().avg_pe, 32.0);
    }

    #[test]
    fn volatility_is_recorded_but_has_no_golden_gate() {
        // Honest limitation. fairvalue.json pins avgPe only; nothing in the
        // golden set pins sector volatility, so these twelve numbers were
        // transferred from SECTOR_CONFIGS at migration and are guarded by
        // this test alone. If a microstructure golden ever carries them, this
        // should become a real cross-check.
        let vols: Vec<f64> = SECTORS.iter().map(|s| s.volatility).collect();
        assert_eq!(vols.len(), 12);
        assert!(vols.iter().all(|v| *v > 0.0 && *v < 3.0));
        assert_eq!(by_key("utilities").unwrap().volatility, 0.6);
        assert_eq!(by_key("energy").unwrap().volatility, 1.3);
    }
}
