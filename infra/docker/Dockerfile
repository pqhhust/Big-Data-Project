# BrainWatch app image — bundles the brainwatch package + scripts onto Spark 3.5.
#
# Build:
#   docker build -f infra/docker/Dockerfile -t brainwatch:0.3.0 .
#
# Push (ECR example):
#   aws ecr get-login-password --region <REGION> | docker login --username AWS --password-stdin <ACCT>.dkr.ecr.<REGION>.amazonaws.com
#   docker tag brainwatch:0.3.0 <ACCT>.dkr.ecr.<REGION>.amazonaws.com/brainwatch:0.3.0
#   docker push <ACCT>.dkr.ecr.<REGION>.amazonaws.com/brainwatch:0.3.0
#
# Push (Docker Hub example):
#   docker tag brainwatch:0.3.0 <DOCKERHUB_USER>/brainwatch:0.3.0
#   docker push <DOCKERHUB_USER>/brainwatch:0.3.0
FROM apache/spark:3.5.4

USER root

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_HOME=/opt/brainwatch

WORKDIR ${APP_HOME}

# Project sources — copy after pyproject so layer caches behave
COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts ./scripts
COPY configs ./configs

# Python deps: project + boto3 + Kafka. PySpark is provided by the base image.
RUN pip install --no-cache-dir -e ".[kafka]" cassandra-driver

# Spark/Kafka/Cassandra connector JARs cached in the image so spark-submit
# doesn't have to re-download on every pod start.
RUN /opt/spark/bin/spark-submit \
      --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0,com.datastax.spark:spark-cassandra-connector_2.12:3.5.0 \
      --conf spark.jars.ivy=/opt/spark/ivy2 \
      /dev/null || true

USER 185
ENTRYPOINT ["/opt/spark/bin/spark-submit"]
