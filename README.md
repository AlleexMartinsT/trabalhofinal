Este projeto é um sistema simples de rastreamento criado com Flask, TinyDB e JWT.
Ele simula um ambiente serverless da AWS usando rotas que funcionam como “Lambdas” e  filas SNS simplificadas.

Funcionalidades

Registro e login com JWT

CRUD de shipments

Adição de eventos manuais

Webhook que publica mensagens em uma “fila SNS”

Processador SNS que cria eventos automaticamente

Healthcheck simples

-Tecnologias

Flask · TinyDB · JWT · Werkzeug

-Principais rotas

POST /auth/register – cria usuário

POST /auth/login – autentica

POST /shipments – cria shipment

GET /shipments – lista

POST /shipments/<id>/events – adiciona evento

POST /webhook – recebe atualizações externas

POST /sns/process – processa fila SNS

GET /health – status 
