FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt /app/

RUN apt-get update && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir -r requirements.txt

COPY . /app/

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
