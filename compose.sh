#!/bin/bash

sudo docker compose down -v
sudo docker compose up --build -d --remove-orphans --force-recreate