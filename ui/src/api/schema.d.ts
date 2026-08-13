

export interface paths {
    "/api/run": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };

        get: operations["get_run_api_run_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/campaigns/starts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };

        get: operations["get_starts_api_campaigns_starts_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/campaigns/matrix": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };

        get: operations["get_matrix_api_campaigns_matrix_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/campaigns": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };

        get: operations["get_campaigns_api_campaigns_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/campaigns/{campaign_key}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };

        get: operations["get_campaign_api_campaigns__campaign_key__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/decisions/actions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };

        get: operations["get_actions_api_decisions_actions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/decisions/menus": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };

        get: operations["get_menus_api_decisions_menus_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/decisions/timeline": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };

        get: operations["get_timeline_api_decisions_timeline_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/decisions/{decision_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };

        get: operations["get_decision_api_decisions__decision_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/decisions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };

        get: operations["get_decisions_api_decisions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/models/forcing": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };

        get: operations["get_forcing_api_models_forcing_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/models/agreement": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };

        get: operations["get_agreement_api_models_agreement_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/models/agreement/series": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };

        get: operations["get_agreement_series_api_models_agreement_series_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/models/agreement/breakdown": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };

        get: operations["get_agreement_breakdown_api_models_agreement_breakdown_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/analytics": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };

        get: operations["get_analytics_api_analytics_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/analytics/rebuild": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;

        post: operations["post_analytics_rebuild_api_analytics_rebuild_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/models/correlations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };

        get: operations["get_correlations_api_models_correlations_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/models/training": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };

        get: operations["get_training_api_models_training_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/models": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };

        get: operations["get_models_api_models_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/infra": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };

        get: operations["get_infra_api_infra_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/infra/kill": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;

        post: operations["post_kill_api_infra_kill_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/infra/launch": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;

        post: operations["post_launch_api_infra_launch_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/infra/coldstart": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;

        post: operations["post_coldstart_api_infra_coldstart_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };

        get: operations["events_api_events_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };

        get: operations["health_api_health_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {

        ActionTypeRow: {
            action_type: components["schemas"]["Ident"];
            rate: components["schemas"]["Rate"];

            refusals?: components["schemas"]["Ident"][];

            state: "ok" | "warn" | "bad" | "neutral";
        };

        ActionsPage: {
            scope: components["schemas"]["Scope"];

            tiles: components["schemas"]["Metric"][];

            by_type: components["schemas"]["ActionTypeRow"][];

            policies: components["schemas"]["PolicyRow"][];

            denominators?: components["schemas"]["Count"][];
        };

        ActivityRow: {

            stream: string;

            last_write?: string | null;

            age_seconds?: number | null;

            state: "ok" | "warn" | "bad" | "neutral";
        };

        AgreementBreakdownPage: {
            scope: components["schemas"]["Scope"];
            freshness: components["schemas"]["AnalyticsFreshness"];

            dim: "arm" | "action_type" | "context_kind";

            rows?: components["schemas"]["AgreementBreakdownRow"][];

            empty_reason?: string | null;
        };

        AgreementBreakdownRow: {
            key: components["schemas"]["Ident"];
            decisions: components["schemas"]["Count"];

            rho_median?: number | null;

            rho_mean?: number | null;

            tau_mean?: number | null;

            rbo_mean?: number | null;
            same_top: components["schemas"]["Rate"];
        };

        AgreementPage: {
            scope: components["schemas"]["Scope"];
            freshness: components["schemas"]["AnalyticsFreshness"];
            correlation?: components["schemas"]["CorrelationSummary"] | null;

            rho_bins?: components["schemas"]["RhoBin"][];

            summary: components["schemas"]["AgreementSummary"][];

            rows: components["schemas"]["AgreementRankRow"][];

            secondary?: components["schemas"]["SecondaryMeasure"][];

            warning?: string | null;

            empty_reason?: string | null;
        };

        AgreementRankRow: {
            picked_by: components["schemas"]["Ident"];

            decisions: number;

            cat_rank?: number | null;

            cat_pct?: number | null;

            gnn_rank?: number | null;

            gnn_pct?: number | null;

            delta_pct?: number | null;

            rho_median?: number | null;

            fell_back: number;
        };

        AgreementSeriesPage: {
            scope: components["schemas"]["Scope"];
            freshness: components["schemas"]["AnalyticsFreshness"];

            axis: "window" | "generation";

            is_alignment: boolean;

            caveat?: string | null;

            bucket_decisions?: number | null;

            min_decisions?: number | null;
            ambiguous: components["schemas"]["Count"];

            points?: components["schemas"]["AgreementSeriesPoint"][];

            generations?: components["schemas"]["GenerationRow"][];

            empty_reason?: string | null;
        };

        AgreementSeriesPoint: {

            label: string;

            seq: number;
            decisions: components["schemas"]["Count"];

            from_decision?: number | null;

            to_decision?: number | null;

            from_ts?: number | null;

            to_ts?: number | null;

            rho_median?: number | null;

            rho_mean?: number | null;

            rho_q1?: number | null;

            rho_q3?: number | null;

            tau_mean?: number | null;

            rbo_mean?: number | null;
            same_top: components["schemas"]["Rate"];

            gate?: string | null;
        };

        AgreementSummary: {

            measure: string;

            value: string;

            help?: string | null;
        };

        AnalyticsFreshness: {

            tenant: string;
            behind: components["schemas"]["Count"];
            rows: components["schemas"]["Count"];

            computed_through?: number | null;

            age_seconds?: number | null;

            formula_version: number;

            state: "ok" | "warn" | "bad" | "neutral";

            detail?: string | null;
        };

        AnalyticsPage: {
            scope: components["schemas"]["Scope"];

            tenants?: components["schemas"]["TenantStatus"][];

            db_path: string;

            runner_hint: string;
        };

        ArmCoverage: {
            screen: components["schemas"]["Ident"];

            rows: number;

            tree_scored: number;

            graph_scored: number;

            both: number;
            agree?: components["schemas"]["Rate"] | null;
        };

        CampaignDetail: {
            scope: components["schemas"]["Scope"];
            row: components["schemas"]["CampaignRow"];

            reward: components["schemas"]["RewardPoint"][];

            constant_columns?: string[];

            diplomacy: components["schemas"]["DiploEvent"][];

            decisions: components["schemas"]["DecisionRow"][];
        };

        CampaignRow: {

            campaign_id: number;
            campaign: components["schemas"]["Ident"];
            campaign_map?: components["schemas"]["Ident"] | null;

            turns?: number | null;

            decisions: number;

            no_action: number;

            attempted: number;

            confirmed: number;
            confirm_rate?: components["schemas"]["Rate"] | null;

            span_min?: number | null;

            peak_settlements?: number | null;

            peak_power_rank?: number | null;

            peak_lord_level?: number | null;

            final_settlements?: number | null;

            final_power_rank?: number | null;

            final_income?: number | null;

            turn_rows: number;

            first_turn?: number | null;

            last_measured_turn?: number | null;

            growth_span_turns?: number | null;

            first_settlements?: number | null;

            first_lord_level?: number | null;

            final_lord_level?: number | null;

            settlements_growth?: number | null;

            lord_growth?: number | null;

            settlements_per_turn?: number | null;

            lord_per_turn?: number | null;

            growth_state: "measured" | "single_turn" | "no_turn_rows";
            outcome?: components["schemas"]["Ident"] | null;

            outcome_state: "ok" | "warn" | "bad" | "neutral";

            ended_because?: string | null;

            suspicious: boolean;

            ended_when?: string | null;
        };

        CampaignsPage: {
            scope: components["schemas"]["Scope"];

            headline: components["schemas"]["OutcomeTally"][];
            suspicious: components["schemas"]["Count"];
            unjoined: components["schemas"]["Count"];
            growth_coverage: components["schemas"]["Rate"];

            rows: components["schemas"]["CampaignRow"][];
        };

        ControlResult: {

            ok: boolean;

            steps: string[];
        };

        CorrelationRow: {
            arm: components["schemas"]["Ident"];

            campaigns: number;

            turns: number;
            share?: components["schemas"]["Rate"] | null;

            per_campaign?: number | null;

            settlements_r?: number | null;

            settlements_gate?: string | null;

            lord_r?: number | null;

            lord_gate?: string | null;
        };

        CorrelationSummary: {
            compared: components["schemas"]["Count"];
            coverage: components["schemas"]["Rate"];

            rho_median?: number | null;

            rho_mean?: number | null;

            rho_q1?: number | null;

            rho_q3?: number | null;

            tau_median?: number | null;

            tau_mean?: number | null;
            same_best: components["schemas"]["Rate"];

            overlap_median?: number | null;

            from_decision?: number | null;

            to_decision?: number | null;

            excluded?: components["schemas"]["Count"][];
        };

        CorrelationTile: {

            label: "action ranker" | "interrupt model";

            rows: components["schemas"]["CorrelationRow"][];
        };

        CorrelationsPage: {
            scope: components["schemas"]["Scope"];

            tiles: components["schemas"]["CorrelationTile"][];
        };

        Count: {

            value: number;

            noun: string;

            population: string;
        };

        Current: {
            campaign?: components["schemas"]["Ident"] | null;

            turn?: number | null;

            settlements?: number | null;

            power_rank?: number | null;

            lord_level?: number | null;

            age_seconds?: number | null;
        };

        DecisionAgreement: {
            n: components["schemas"]["Count"];

            status: string;

            rho?: number | null;

            tau_b?: number | null;

            rbo?: number | null;

            top1_same?: boolean | null;

            top3_overlap?: number | null;

            cat_top_in_gnn?: number | null;

            gnn_top_in_cat?: number | null;

            note?: string | null;
        };

        DecisionDetail: {
            scope: components["schemas"]["Scope"];
            row: components["schemas"]["DecisionRow"];
            agreement?: components["schemas"]["DecisionAgreement"] | null;

            offers: components["schemas"]["OfferRow"][];

            entities: components["schemas"]["EntityState"][];

            phases: components["schemas"]["PhaseSpan"][];
        };

        DecisionRow: {

            decision_id: number;

            ts?: number | null;
            campaign?: components["schemas"]["Ident"] | null;

            turn?: number | null;

            offers?: number | null;

            entity?: string | null;
            action_type?: components["schemas"]["Ident"] | null;

            action_key?: string | null;
            result?: components["schemas"]["Ident"] | null;

            result_state: "ok" | "warn" | "bad" | "neutral";
            refusal?: components["schemas"]["Ident"] | null;
            policy?: components["schemas"]["Ident"] | null;

            exploit?: number | null;

            pct_global?: number | null;

            pct_local?: number | null;

            cat_rank?: number | null;

            gnn_impact?: number | null;

            gnn_rank?: number | null;

            delta_pct?: number | null;

            rho?: number | null;

            rho_n?: number | null;

            latency_ms?: number | null;
        };

        DecisionsPage: {
            scope: components["schemas"]["Scope"];
            total: components["schemas"]["Count"];

            offset: number;

            limit: number;

            action_types: components["schemas"]["Ident"][];

            policies: components["schemas"]["Ident"][];

            results: components["schemas"]["Ident"][];

            rows: components["schemas"]["DecisionRow"][];
        };

        DiploEvent: {

            turn?: number | null;
            channel?: components["schemas"]["Ident"] | null;
            faction?: components["schemas"]["Ident"] | null;
            outcome?: components["schemas"]["Ident"] | null;

            deal_score?: number | null;

            standing?: number | null;

            terms?: string | null;

            state: "ok" | "warn" | "bad" | "neutral";
        };

        EntityState: {

            context_kind: string;

            context_id: string;

            features: {
                [key: string]: unknown;
            };
        };

        FitConfigRow: {

            family: string;

            role: string;

            hyperparameters: {
                [key: string]: unknown;
            };

            compute: {
                [key: string]: unknown;
            };
        };

        ForcingBar: {
            action_type: components["schemas"]["Ident"];
            share: components["schemas"]["Rate"];

            ci_lo?: number | null;

            ci_hi?: number | null;
        };

        ForcingPage: {
            scope: components["schemas"]["Scope"];
            decisions: components["schemas"]["Count"];

            tiles: components["schemas"]["ForcingTile"][];

            empty_reason?: string | null;
        };

        ForcingTile: {

            model: string;
            favours?: components["schemas"]["Ident"] | null;

            bars: components["schemas"]["ForcingBar"][];
        };

        GenerationRow: {
            trial: components["schemas"]["Ident"];

            generation?: number | null;

            retrained: boolean;

            from_ts?: number | null;

            to_ts?: number | null;

            overlapped_by?: string | null;
            decisions: components["schemas"]["Count"];

            rho_median?: number | null;

            rho_mean?: number | null;

            tau_mean?: number | null;

            rbo_mean?: number | null;
            same_top: components["schemas"]["Rate"];
        };

        HTTPValidationError: {

            detail?: components["schemas"]["ValidationError"][];
        };

        Ident: {

            raw: string;

            label: string;

            culture?: string | null;

            tag?: string | null;
        };

        InfraPage: {
            scope: components["schemas"]["Scope"];

            services: components["schemas"]["Service"][];

            activity: components["schemas"]["ActivityRow"][];

            policy_note: string;

            models: string[];
            defaults: components["schemas"]["LaunchDefaults"];
            cold_defaults: components["schemas"]["LaunchDefaults"];

            log_tail: string[];
        };

        InterruptOption: {
            label: components["schemas"]["Ident"];

            exploit?: number | null;

            gnn?: number | null;

            chosen: boolean;
        };

        InterruptRow: {

            interrupt_id: number;

            ts?: number | null;
            kind: components["schemas"]["Ident"];

            root?: string | null;
            campaign?: components["schemas"]["Ident"] | null;

            turn?: number | null;
            result?: components["schemas"]["Ident"] | null;

            result_state: "ok" | "warn" | "bad" | "neutral";
            chosen?: components["schemas"]["Ident"] | null;

            n_options?: number | null;
            policy?: components["schemas"]["Ident"] | null;

            latency_ms?: number | null;

            options?: components["schemas"]["InterruptOption"][];
        };

        LaunchDefaults: {

            campaigns: number;

            turns_min: number;

            turns_max: number;

            retrain_first: boolean;

            retrain_every: number;

            model: string;

            cfg: string;

            strategies: string;

            ruleset: string;

            dev: boolean;
        };

        MatrixCell: {
            action_type: components["schemas"]["Ident"];
            rate: components["schemas"]["Rate"];

            total_ms?: number | null;

            per_try_ms?: number | null;

            state: "ok" | "warn" | "bad" | "neutral";
        };

        MatrixPage: {
            scope: components["schemas"]["Scope"];

            kind: "action" | "interrupt";

            totals: components["schemas"]["MatrixTotal"][];

            columns: components["schemas"]["Ident"][];

            rows: components["schemas"]["MatrixRow"][];
        };

        MatrixRow: {
            faction: components["schemas"]["Ident"];

            cells: components["schemas"]["MatrixCell"][];
        };

        MatrixTotal: {
            action_type: components["schemas"]["Ident"];
            rate: components["schemas"]["Rate"];

            total_ms?: number | null;

            per_try_ms?: number | null;

            state: "ok" | "warn" | "bad" | "neutral";
        };

        MenusPage: {
            scope: components["schemas"]["Scope"];
            total: components["schemas"]["Count"];

            by_screen: components["schemas"]["Count"][];

            policies: components["schemas"]["PolicyRow"][];

            coverage: components["schemas"]["ArmCoverage"][];

            rows: components["schemas"]["InterruptRow"][];
        };

        Metric: {

            label: string;

            value?: number | string | null;

            unit?: string | null;

            sub?: string | null;

            state: "ok" | "warn" | "bad" | "neutral";

            spark?: number[];
        };

        ModelCard: {

            name: string;

            role: string;

            status: "ready" | "missing" | "incomplete" | "stale schema";

            state: "ok" | "warn" | "bad" | "neutral";

            rows?: [
                string,
                string
            ][];

            note?: string | null;

            trained_at?: string | null;
        };

        ModelsPage: {
            scope: components["schemas"]["Scope"];

            cards: components["schemas"]["ModelCard"][];

            fit: components["schemas"]["FitConfigRow"][];
        };

        OfferRow: {

            rank?: number | null;

            entity?: string | null;
            action_type?: components["schemas"]["Ident"] | null;

            action_key?: string | null;

            exploit?: number | null;

            pct_global?: number | null;

            pct_local?: number | null;

            gnn_impact?: number | null;

            gnn_rank?: number | null;

            taken: boolean;
        };

        OutcomeTally: {
            outcome: components["schemas"]["Ident"];

            count: number;

            state: "ok" | "warn" | "bad" | "neutral";
        };

        PhaseSpan: {

            phase: "collect" | "queue" | "score" | "verify";

            ms: number;
        };

        PolicyRow: {
            policy: components["schemas"]["Ident"];

            picks: number;
            share: components["schemas"]["Rate"];

            note?: string | null;
        };

        Rate: {

            n: number;

            of: number;

            noun: string;

            population: string;
        };

        RewardPoint: {

            turn: number;

            income?: number | null;

            settlements?: number | null;

            allies?: number | null;

            vassals?: number | null;

            power_rank?: number | null;
        };

        RhoBin: {

            lo: number;

            hi: number;

            decisions: number;
        };

        RunPage: {
            scope: components["schemas"]["Scope"];

            services: components["schemas"]["Service"][];
            current: components["schemas"]["Current"];

            throughput: components["schemas"]["Metric"][];

            totals: components["schemas"]["Count"][];

            collect_timing: components["schemas"]["TimingRow"][];

            cycle_timing: components["schemas"]["TimingRow"][];

            log_tail: string[];

            log_name?: string | null;
        };

        Scope: {

            text: string;

            detail?: string | null;
        };

        SecondaryMeasure: {

            measure: string;

            value: string;
            rate?: components["schemas"]["Rate"] | null;
        };

        Service: {

            name: string;

            up: boolean;

            pid?: number | null;

            started?: string | null;

            detail?: string | null;
        };

        StartRow: {
            faction: components["schemas"]["Ident"];

            n: number;

            single_sample: boolean;

            avg_turns?: number | null;

            best_turns?: number | null;

            best_settlements?: number | null;

            best_power_rank?: number | null;

            best_lord_level?: number | null;

            ever_allied: number;

            ever_vassal: number;
            confirm_rate?: components["schemas"]["Rate"] | null;
        };

        StartsPage: {
            scope: components["schemas"]["Scope"];
            low_sample: components["schemas"]["Count"];

            rows: components["schemas"]["StartRow"][];
        };

        TenantStatus: {

            tenant: string;

            formula_version: number;
            rows: components["schemas"]["Count"];
            behind: components["schemas"]["Count"];

            watermark?: number | null;

            built?: string | null;

            last_run?: string | null;

            last_run_seconds?: number | null;

            last_error?: string | null;

            state: "ok" | "warn" | "bad" | "neutral";
        };

        TimelineAction: {

            decision_id: number;
            action_type?: components["schemas"]["Ident"] | null;

            action_key?: string | null;
            result?: components["schemas"]["Ident"] | null;

            result_state: "ok" | "warn" | "bad" | "neutral";

            phases: components["schemas"]["PhaseSpan"][];

            total_ms?: number | null;

            gap_ms?: number | null;

            unaccounted_ms?: number | null;
        };

        TimelineLane: {
            campaign: components["schemas"]["Ident"];

            turn: number;
            confirmed: components["schemas"]["Rate"];

            in_turn_s?: number | null;

            actions: components["schemas"]["TimelineAction"][];
        };

        TimelinePage: {
            scope: components["schemas"]["Scope"];

            phase_legend: string[];

            lanes: components["schemas"]["TimelineLane"][];
        };

        TimingRow: {

            stage: string;

            median_ms?: number | null;

            max_ms?: number | null;

            state: "ok" | "warn" | "bad" | "neutral";
        };

        TrainingEvent: {

            when?: string | null;

            trial?: string | null;

            corpus_rows?: number | null;

            corpus_campaigns?: number | null;

            groups?: {
                [key: string]: unknown;
            };
        };

        TrainingPage: {
            scope: components["schemas"]["Scope"];

            trials: components["schemas"]["TrialRow"][];

            history: components["schemas"]["TrainingEvent"][];

            group_order: string[];
        };

        TrialCorr: {

            r?: number | null;

            gate?: string | null;
            over: components["schemas"]["Count"];
        };

        TrialRow: {

            trial: string;

            snapshots: number;

            backend?: string | null;

            cfg?: string | null;

            mix?: {
                [key: string]: unknown;
            };

            ruleset?: string | null;

            campaigns?: number | null;

            corpus?: number | null;

            settlements_per_campaign?: number | null;

            settlements_total?: number | null;
            grew?: components["schemas"]["Rate"] | null;
            shrank?: components["schemas"]["Rate"] | null;

            growth_baseline?: string | null;

            lord_per_campaign?: number | null;

            turns_per_campaign?: number | null;

            seconds_per_campaign?: number | null;

            seconds_per_turn?: number | null;

            notes?: string | null;

            live: boolean;

            growth_corr?: {
                [key: string]: components["schemas"]["TrialCorr"];
            };
        };

        ValidationError: {

            loc: (string | number)[];

            msg: string;

            type: string;

            input?: unknown;

            ctx?: Record<string, never>;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    get_run_api_run_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunPage"];
                };
            };
        };
    };
    get_starts_api_campaigns_starts_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["StartsPage"];
                };
            };
        };
    };
    get_matrix_api_campaigns_matrix_get: {
        parameters: {
            query?: {
                kind?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MatrixPage"];
                };
            };

            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_campaigns_api_campaigns_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CampaignsPage"];
                };
            };
        };
    };
    get_campaign_api_campaigns__campaign_key__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                campaign_key: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CampaignDetail"];
                };
            };

            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_actions_api_decisions_actions_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ActionsPage"];
                };
            };
        };
    };
    get_menus_api_decisions_menus_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MenusPage"];
                };
            };
        };
    };
    get_timeline_api_decisions_timeline_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TimelinePage"];
                };
            };
        };
    };
    get_decision_api_decisions__decision_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                decision_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DecisionDetail"];
                };
            };

            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_decisions_api_decisions_get: {
        parameters: {
            query?: {
                offset?: number;
                limit?: number;
                action_type?: string | null;
                policy?: string | null;
                result?: string | null;
                campaign?: string | null;
                search?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DecisionsPage"];
                };
            };

            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_forcing_api_models_forcing_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ForcingPage"];
                };
            };
        };
    };
    get_agreement_api_models_agreement_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AgreementPage"];
                };
            };
        };
    };
    get_agreement_series_api_models_agreement_series_get: {
        parameters: {
            query?: {
                axis?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AgreementSeriesPage"];
                };
            };

            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_agreement_breakdown_api_models_agreement_breakdown_get: {
        parameters: {
            query?: {
                dim?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AgreementBreakdownPage"];
                };
            };

            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_analytics_api_analytics_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AnalyticsPage"];
                };
            };
        };
    };
    post_analytics_rebuild_api_analytics_rebuild_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ControlResult"];
                };
            };
        };
    };
    get_correlations_api_models_correlations_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CorrelationsPage"];
                };
            };
        };
    };
    get_training_api_models_training_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TrainingPage"];
                };
            };
        };
    };
    get_models_api_models_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ModelsPage"];
                };
            };
        };
    };
    get_infra_api_infra_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InfraPage"];
                };
            };
        };
    };
    post_kill_api_infra_kill_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ControlResult"];
                };
            };
        };
    };
    post_launch_api_infra_launch_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LaunchDefaults"];
            };
        };
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ControlResult"];
                };
            };

            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    post_coldstart_api_infra_coldstart_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LaunchDefaults"];
            };
        };
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ControlResult"];
                };
            };

            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    events_api_events_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
    health_api_health_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {

            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
}
