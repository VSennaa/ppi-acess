# Arquitetura Django para ALPR em tempo real

## 1) Modelagem inicial (`models.py`)

A modelagem proposta está em `alpr_app/models.py` com quatro entidades:

- `Servidor`: cadastro principal de pessoas autorizadas.
- `PlacaAutorizada`: uma ou várias placas vinculadas a cada servidor.
- `Configuracao`: parâmetros da câmera RTSP e regra de tolerância de match (80-90%).
- `EventoLeitura`: trilha de auditoria para cada decisão (`autorizado`/`desconhecido`).

## 2) Processamento em segundo plano (OpenCV/EasyOCR)

Para evitar travar o Django:

1. Criar um worker dedicado (`RecognitionWorker`) em thread separada.
2. Ler `Configuracao` ativa do banco.
3. Capturar stream RTSP com OpenCV.
4. Quando detectar veículo parado, executar **múltiplas tentativas de OCR** em sequência.
5. Consolidar resultado por votação/frequência + confiança média.
6. Aplicar match percentual com placas autorizadas.
7. Persistir em `EventoLeitura`.
8. Publicar status em WebSocket para o frontend.

Arquivo base: `alpr_app/services/recognition_worker.py`.

> Em produção, a opção mais robusta é mover esse worker para processo separado (Celery worker, RQ worker ou management command dedicado), em vez de iniciar dentro do `runserver`/ASGI.

## 3) Tempo real no frontend com Django Channels

- Consumer WebSocket: `alpr_app/consumers.py`
- Roteamento WS: `alpr_app/routing.py`
- Grupo: `placas_status`

Cada evento enviado inclui:

- `status`: `autorizado` ou `desconhecido`
- `placa`
- `score`
- `servidor` (quando autorizado)
- `timestamp`

## 4) Vídeo em tempo real

Você tem duas estratégias viáveis:

### Opção A — MJPEG com `StreamingHttpResponse` (mais simples)

- Uma view HTTP gera frames JPEG em multipart stream.
- Frontend mostra com `<img src="/camera/live/">`.
- Menor complexidade, mas maior uso de banda.

### Opção B — WebRTC (mais eficiente e escalável)

- Melhor latência e compressão.
- Exige stack adicional de signaling/STUN/TURN.
- Mais indicado para múltiplos clientes simultâneos.

## 5) Recomendações de produção

- **Separar processos**: ASGI (websocket/http) e worker de visão computacional.
- **Redis channel layer** para Channels.
- **Resiliência RTSP**: reconexão automática quando stream cair.
- **Debounce de eventos**: evitar múltiplos eventos idênticos em poucos segundos.
- **Métricas/logs**: taxa de frames, confiança média OCR, latência por evento.

## 6) Próximos passos

1. Migrar lógica real do `main.py` para `_coletar_tentativas_ocr` e etapa de detecção.
2. Criar endpoint de administração para `Configuracao` e `Servidor`.
3. Criar tela com vídeo + painel de status em websocket.
4. Subir Redis + Daphne/Uvicorn + worker dedicado.
