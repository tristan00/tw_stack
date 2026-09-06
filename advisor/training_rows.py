import pickle
import sys
import tempfile
import time

import numpy as np


class TrainingRows:

    def __init__(self, features):
        self.features = features
        self.file = tempfile.TemporaryFile()
        self.pending = []
        self.columns = {}
        self.size = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.file.close()

    def append(self, row):
        self.pending.append(row)
        self.size += 1
        if len(self.pending) >= 512:
            self.flush()

    def flush(self):
        if not self.pending:
            return
        num, cat = self.features.split_columns(self.pending)
        for key in num:
            self.columns.setdefault(key, True)
        for key in cat:
            self.columns[key] = False
        pickle.dump(self.pending, self.file, protocol=pickle.HIGHEST_PROTOCOL)
        self.pending.clear()

    def pool(self, y):
        from catboost import FeaturesData, Pool
        started = time.perf_counter()
        self.flush()
        num = sorted(k for k, numeric in self.columns.items() if numeric)
        cat = sorted(k for k, numeric in self.columns.items() if not numeric)
        if not self.size:
            return None, num, cat
        numeric = np.empty((self.size, len(num)), dtype=np.float32)
        categorical = np.empty((self.size, len(cat)), dtype=object)
        vocab = [{} for _ in cat]
        self.file.seek(0)
        offset = 0
        while offset < self.size:
            rows = pickle.load(self.file)
            end = offset + len(rows)
            for j, key in enumerate(num):
                numeric[offset:end, j] = [self.features._f(r.get(key)) for r in rows]
            for j, key in enumerate(cat):
                values = []
                for row in rows:
                    value = row.get(key)
                    value = "?" if value is None else str(value)
                    encoded = vocab[j].get(value)
                    if encoded is None:
                        encoded = vocab[j][value] = value.encode("utf-8")
                    values.append(encoded)
                categorical[offset:end, j] = values
            offset = end
        pool = Pool(FeaturesData(num_feature_data=numeric if num else None,
                                 cat_feature_data=categorical if cat else None), label=y)
        sys.stderr.write("training_rows.pool exit %.3fs rows=%d numeric=%d categorical=%d\n"
                         % (time.perf_counter() - started, self.size, len(num), len(cat)))
        return pool, num, cat
