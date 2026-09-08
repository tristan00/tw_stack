import optuna

from advisor.mapgraph.sequence_config import SPACE, history_indices, suggest


class Trial:

    def suggest_categorical(self, name, choices):
        return choices[-1]

    def suggest_float(self, name, low, high, **options):
        return high

    def suggest_int(self, name, low, high, **options):
        return high


def test_reconstruction_mask_and_augmented_objectives():
    for objective in SPACE["representation_objective"]:
        params = dict(suggest(Trial()), representation_objective=objective, mask=0.2)
        trial = optuna.trial.FixedTrial(params)
        config = suggest(trial)
        assert config["mask"] == (0.0 if objective == "reconstruct" else 0.2)
        assert ("mask" in trial.params) == (objective != "reconstruct")


def test_history_does_not_cross_campaigns_or_include_current_action():
    assert history_indices(["a", "b", "a", "a", "b", "a"], 2) == [[], [], [0], [0, 2], [1], [2, 3]]
    assert history_indices(["a", "a"], 0) == [[], []]


def test_search_supports_transformer_width_and_phase_limits():
    config = suggest(Trial())
    assert config["sequence_kind"] == "transformer"
    assert config["sequence_hidden"] % 4 == 0
    assert 0 < config["encoder_fraction"] < 1


def test_requested_integer_ranges():
    trial = optuna.trial.FixedTrial(suggest(Trial()))
    suggest(trial)
    for name in ("latent", "sequence_hidden", "representation_hidden"):
        distribution = trial.distributions[name]
        assert (distribution.low, distribution.high, distribution.step) == (4, 64, 4)
    distribution = trial.distributions["history_length"]
    assert (distribution.low, distribution.high, distribution.step) == (1, 64, 1)
