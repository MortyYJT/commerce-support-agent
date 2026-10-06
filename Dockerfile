FROM python:3.11.17-slim-bookworm@sha256:2333bd330d12de02514770b3585cad313644316047cdee24a7acfdece6de6efb AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build

COPY pyproject.toml requirements.txt ./
COPY src/commerce_support ./src/commerce_support

RUN python -m pip install --no-cache-dir --no-deps setuptools==84.0.0 \
    && python -m pip wheel --no-deps --no-build-isolation --wheel-dir=/wheelhouse .

FROM python:3.11.17-slim-bookworm@sha256:2333bd330d12de02514770b3585cad313644316047cdee24a7acfdece6de6efb AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN addgroup --system --gid 10001 commerce \
    && adduser --system --uid 10001 --ingroup commerce --home /app commerce

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY --from=builder /wheelhouse/commerce_support_agent-0.1.0-py3-none-any.whl /tmp/commerce_support_agent-0.1.0-py3-none-any.whl
RUN python -m pip install --no-cache-dir --no-deps /tmp/commerce_support_agent-0.1.0-py3-none-any.whl \
    && rm /tmp/commerce_support_agent-0.1.0-py3-none-any.whl

USER commerce
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "commerce_support.app:app", "--host", "0.0.0.0", "--port", "8000"]
