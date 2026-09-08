SPACE = {
    "representation_objective": ["denoise", "reconstruct", "contrastive", "hybrid"],
    "sequence_kind": ["gru", "lstm", "transformer"],
    "sequence_layers": [1, 2, 3],
    "sequence_dropout": [0.0, 0.1, 0.25],
}

FIXED = {"bf16": True, "seed": 0, "device": "cuda"}


def suggest(trial):
    config = {name: trial.suggest_categorical(name, values) for name, values in SPACE.items()}
    config["mask"] = (0.0 if config["representation_objective"] == "reconstruct"
                      else trial.suggest_categorical("mask", [0.1, 0.2, 0.3]))
    config["batch"] = trial.suggest_categorical("batch", [128, 256, 512])
    config["representation_hidden"] = trial.suggest_int("representation_hidden", 4, 64, step=4)
    config["latent"] = trial.suggest_int("latent", 4, 64, step=4)
    config["ssl_epochs"] = trial.suggest_int("ssl_epochs", 1, 30)
    config["ssl_patience"] = trial.suggest_int("ssl_patience", 1, 10)
    config["encoder_fraction"] = trial.suggest_float("encoder_fraction", 0.2, 0.8, step=0.1)
    config["sequence_hidden"] = trial.suggest_int("sequence_hidden", 4, 64, step=4)
    config["history_length"] = trial.suggest_int("history_length", 1, 64, step=1)
    config["sequence_lr"] = trial.suggest_float("sequence_lr", 1e-5, 1e-3, log=True)
    config["sequence_weight_decay"] = trial.suggest_float("sequence_weight_decay", 1e-6, 1e-2, log=True)
    config["sequence_epochs"] = trial.suggest_int("sequence_epochs", 5, 120, step=5)
    config["sequence_patience"] = trial.suggest_int("sequence_patience", 2, 15)
    config["sequence_batch"] = trial.suggest_categorical("sequence_batch", [128, 256, 512])
    return dict(FIXED, **config)


def history_indices(groups, length):
    histories, result = {}, []
    for index, group in enumerate(groups):
        previous = histories.setdefault(group, [])
        result.append(previous[-length:] if length else [])
        previous.append(index)
    return result
