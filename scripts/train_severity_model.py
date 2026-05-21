#!/usr/bin/env python3
"""Spark MLlib · train a logistic-regression on gold/patient_features.

Predicts a synthetic binary "high anomaly day" label (n_eeg_chunks >= 20)
from `mean_sampling_rate`, `has_critical_lab_today`, `n_medication_changes`
features. Demonstrates the *Advanced Analytics → MLlib* checkbox on the
IT4043E rubric end-to-end (split, VectorAssembler, fit, evaluate, save model).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold",  type=Path, default=Path("data/lake/gold"))
    parser.add_argument("--model", type=Path, default=Path("artifacts/models/severity_logreg"))
    parser.add_argument("--metrics-out", type=Path, default=Path("dashboard/public/insights/model_metrics.json"))
    parser.add_argument("--driver-memory", default="8g")
    args = parser.parse_args()

    from pyspark.sql import SparkSession, functions as F
    from pyspark.ml.feature import VectorAssembler
    from pyspark.ml.classification import LogisticRegression
    from pyspark.ml.evaluation import BinaryClassificationEvaluator

    spark = (
        SparkSession.builder
        .appName("brainwatch-mllib")
        .master("local[8]")
        .config("spark.driver.memory", args.driver_memory)
        .config("spark.sql.shuffle.partitions", "16")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")

    df = spark.read.parquet(str(args.gold / "patient_features"))
    df = df.withColumn("label", (F.col("n_eeg_chunks") >= 20).cast("int"))
    df = df.fillna({
        "mean_sampling_rate": 200.0,
        "has_critical_lab_today": 0,
        "n_medication_changes": 0,
    })

    assembler = VectorAssembler(
        inputCols=["mean_sampling_rate", "has_critical_lab_today", "n_medication_changes"],
        outputCol="features",
        handleInvalid="skip",
    )
    feat = assembler.transform(df)

    train, test = feat.randomSplit([0.8, 0.2], seed=42)
    lr = LogisticRegression(maxIter=20, regParam=0.0, elasticNetParam=0.0,
                            featuresCol="features", labelCol="label")
    model = lr.fit(train)

    pred = model.transform(test)
    evaluator = BinaryClassificationEvaluator(rawPredictionCol="rawPrediction", labelCol="label",
                                              metricName="areaUnderROC")
    auc = float(evaluator.evaluate(pred))
    accuracy = float(pred.filter(F.col("label") == F.col("prediction")).count()) / float(pred.count())

    confusion = (
        pred.groupBy("label", "prediction").count().collect()
    )
    confusion = sorted([(int(r["label"]), int(r["prediction"]), int(r["count"])) for r in confusion])

    args.model.parent.mkdir(parents=True, exist_ok=True)
    model.write().overwrite().save(str(args.model))

    metrics = {
        "auc_roc":            round(auc, 4),
        "accuracy":           round(accuracy, 4),
        "train_count":        train.count(),
        "test_count":         pred.count(),
        "coefficients":       [round(float(c), 5) for c in model.coefficients.toArray()],
        "intercept":          round(float(model.intercept), 5),
        "features":           ["mean_sampling_rate", "has_critical_lab_today", "n_medication_changes"],
        "confusion_matrix":   [{"label": l, "prediction": p, "count": c} for (l, p, c) in confusion],
        "model_path":         str(args.model),
    }
    args.metrics_out.parent.mkdir(parents=True, exist_ok=True)
    args.metrics_out.write_text(json.dumps(metrics, indent=2))
    print(json.dumps(metrics, indent=2))

    spark.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
