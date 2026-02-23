import asyncio
import threading
import time
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable, Optional

import cv2

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils import timezone

from alpr_app.models import Configuracao, EventoLeitura, PlacaAutorizada


@dataclass
class OCRResult:
    valor: str
    confianca: float


def normalizar_placa(raw: str) -> str:
    return "".join(ch for ch in raw.upper() if ch.isalnum())


def similaridade_percentual(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() * 100


class RecognitionWorker:
    """
    Worker em thread dedicada para não travar o loop do Django.

    Estratégia:
    1. Captura frames continuamente.
    2. Detecta veículo/placa (placeholder para sua pipeline OpenCV + Caffe atual).
    3. Faz múltiplas tentativas de OCR enquanto o mesmo evento está ativo.
    4. Consolida por votação (maior frequência + média de confiança).
    5. Persiste no banco e publica evento em WebSocket.
    """

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        config = Configuracao.objects.filter(ativo=True).first()
        if not config:
            return

        cap = cv2.VideoCapture(config.rtsp_url)
        channel_layer = get_channel_layer()

        try:
            while not self._stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    time.sleep(1)
                    continue

                # TODO: substituir por detecção real do seu main.py.
                vehicle_present = True
                if not vehicle_present:
                    time.sleep(config.intervalo_frames_ms / 1000)
                    continue

                tentativa_results = self._coletar_tentativas_ocr(
                    frame=frame,
                    tentativas=config.tentativas_por_evento,
                    intervalo_ms=config.intervalo_frames_ms,
                )

                consolidado = self._consolidar_tentativas(tentativa_results)
                if not consolidado:
                    continue

                match = self._buscar_autorizado(
                    placa_lida=consolidado.valor,
                    tolerancia=config.tolerancia_match_percentual,
                )

                evento = EventoLeitura.objects.create(
                    placa_lida=consolidado.valor,
                    placa_normalizada=normalizar_placa(consolidado.valor),
                    confianca_ocr=consolidado.confianca,
                    score_match_percentual=match[1] if match else 0,
                    status=(
                        EventoLeitura.Status.AUTORIZADO
                        if match
                        else EventoLeitura.Status.DESCONHECIDO
                    ),
                    servidor=match[0].servidor if match else None,
                )

                async_to_sync(channel_layer.group_send)(
                    "placas_status",
                    {
                        "type": "placa.status",
                        "payload": {
                            "status": evento.status,
                            "placa": evento.placa_lida,
                            "score": evento.score_match_percentual,
                            "servidor": evento.servidor.nome if evento.servidor else None,
                            "timestamp": timezone.localtime(evento.criado_em).isoformat(),
                        },
                    },
                )

                time.sleep(config.intervalo_frames_ms / 1000)
        finally:
            cap.release()

    def _coletar_tentativas_ocr(
        self,
        frame,
        tentativas: int,
        intervalo_ms: int,
    ) -> Iterable[OCRResult]:
        resultados = []
        for _ in range(tentativas):
            # TODO: plugar EasyOCR real aqui. Simulação mínima:
            texto_lido = "ABC1D23"
            confianca = 0.86
            if texto_lido:
                resultados.append(OCRResult(valor=texto_lido, confianca=confianca))
            time.sleep(intervalo_ms / 1000)

        return resultados

    def _consolidar_tentativas(self, resultados: Iterable[OCRResult]) -> Optional[OCRResult]:
        resultados = list(resultados)
        if not resultados:
            return None

        votos = Counter(normalizar_placa(r.valor) for r in resultados)
        placa_vencedora = votos.most_common(1)[0][0]
        amostras = [r for r in resultados if normalizar_placa(r.valor) == placa_vencedora]
        confianca_media = sum(r.confianca for r in amostras) / len(amostras)

        return OCRResult(valor=placa_vencedora, confianca=confianca_media)

    def _buscar_autorizado(self, placa_lida: str, tolerancia: int):
        placa_lida = normalizar_placa(placa_lida)
        candidatos = PlacaAutorizada.objects.select_related("servidor").filter(servidor__ativo=True)

        melhor = None
        melhor_score = 0.0
        for cand in candidatos:
            score = similaridade_percentual(placa_lida, normalizar_placa(cand.valor))
            if score > melhor_score:
                melhor = cand
                melhor_score = score

        if melhor and melhor_score >= tolerancia:
            return melhor, melhor_score

        return None


worker = RecognitionWorker()


async def start_worker_async() -> None:
    await asyncio.to_thread(worker.start)
