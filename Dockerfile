FROM python:3.12-slim

# System dependencies, including curl
RUN apt-get update && apt-get install -y \
    net-tools \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Poetry using official installer
RUN curl -sSL https://install.python-poetry.org | python3 -

# Add Poetry to PATH
ENV PATH="/root/.local/bin:$PATH"

# Copy dependency files
COPY pyproject.toml poetry.lock ./

# Install dependencies
RUN poetry config virtualenvs.create false && \
    poetry install --only main --no-interaction --no-ansi

# Copy application code
COPY . .

# Set Python to run in unbuffered mode
ENV PYTHONUNBUFFERED=1
ENV PYTHONTRACEMALLOC=1

EXPOSE 5000

# Run the service
CMD ["poetry", "run", "start"]