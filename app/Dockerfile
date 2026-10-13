# Use the official Python image from Docker Hub
FROM python:3.11.15

# Set the working directory
WORKDIR /app

# Copy only the requirements file first to leverage Docker layer caching
COPY requirements.txt /app/

# Install dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/* && \
    pip3 install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . /app

# The API (server.py) listens here; docker-compose publishes it.
EXPOSE 8000

# Command to keep the container running
CMD ["tail", "-f", "/dev/null"]
