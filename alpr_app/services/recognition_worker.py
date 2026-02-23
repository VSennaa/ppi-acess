import asyncio
import threading
import time
import os
import csv
import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable, Optional

import cv2

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils import timezone

from alpr_app.models import Configuracao, EventoLeitura


# 1️⃣ Variável global para a view consumir o frame atual
last_frame = None
#os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;udp'


@dataclass
class OCRResult:
    valor: str
    confianca: float


def normalizar_placa(raw: str) -> str:
    return re.sub(r'[^A-Z0-9]', '', str(raw).upper())


def similaridade_percentual(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() * 100


def load_authorized_vehicles(csv_path="servidores.csv"):
    if not os.path.exists(csv_path):
        return {}

    vehicles = {}
    try:
        with open(csv_path, mode='r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f, delimiter=';')
            for row in reader:
                if 'PLACA' in row and 'SERVIDOR' in row:
                    plate = normalizar_placa(row['PLACA'])
                    server_info = row['SERVIDOR'].strip()
                    if plate:
                        vehicles[plate] = server_info
    except Exception as e:
        print(f"[ERRO CSV] Falha ao ler o arquivo {csv_path}: {e}")

    return vehicles


class RecognitionWorker:
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

    def _try_connect_with_timeout(self, url: str, timeout_sec: int = 8) -> Optional[cv2.VideoCapture]:
        """Tenta conectar com timeout usando threading"""
        cap_container = [None]
        exception_container = [None]
        
        def connect():
            try:
                if isinstance(url, str) and url.startswith("rtsp"):
                    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
                    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
                else:
                    cap = cv2.VideoCapture(int(url) if isinstance(url, str) else url)
                
                cap_container[0] = cap
            except Exception as e:
                exception_container[0] = e
        
        # Tenta conexão em thread separada
        connect_thread = threading.Thread(target=connect, daemon=True)
        connect_thread.start()
        connect_thread.join(timeout=timeout_sec)
        
        if cap_container[0] and cap_container[0].isOpened():
            return cap_container[0]
        
        return None

    def _run(self) -> None:
        global last_frame
        config = Configuracao.objects.filter(ativo=True).first()
        if not config:
            print("[ERRO] Nenhuma configuração ativa encontrada no banco de dados")
            return

        camera_source = 0 if str(config.rtsp_url).strip() == "0" else config.rtsp_url
        
        # Log da URL (mascarando credenciais se existirem)
        url_display = str(camera_source).replace(camera_source.split('@')[0].split('//')[-1], "***") if isinstance(camera_source, str) and '@' in camera_source else camera_source
        print(f"[INFO] Configuração carregada. Camera source: {url_display}")

        channel_layer = get_channel_layer()
        
        # Variáveis de controle para retry
        consecutive_failures = 0
        max_consecutive_failures = 5
        url_index = 0
        urls_to_try = []

        # Se for RTSP, gera lista de URLs para tentar
        if isinstance(camera_source, str) and camera_source.startswith("rtsp"):
            urls_to_try = self._generate_rtsp_url_variations(camera_source)
            print(f"[INFO] Geradas {len(urls_to_try)} variações de URL para testar")
        else:
            urls_to_try = [camera_source]

        while not self._stop_event.is_set():
            cap = None
            
            # Seleciona qual URL testar (rotaciona entre elas)
            if urls_to_try:
                current_url = urls_to_try[url_index % len(urls_to_try)]
                url_index += 1
            else:
                current_url = camera_source
            
            try:
                # Formata URL para log (mascara credenciais)
                log_url = current_url.replace(current_url.split('@')[0].split('//')[-1], "***") if isinstance(current_url, str) and '@' in current_url else current_url
                print(f"[INFO] Tentativa {consecutive_failures + 1}: {log_url}")
                
                # Tenta conectar com timeout de 8 segundos
                cap = self._try_connect_with_timeout(current_url, timeout_sec=8)

                if cap is None:
                    print(f"[AVISO] Falha ao conectar ao stream (timeout ou erro)")
                    time.sleep(1)
                    consecutive_failures += 1
                    continue

                # Se conectou, reseta o contador de falhas
                consecutive_failures = 0
                print(f"[INFO] ✓ Câmera conectada com sucesso!")
                print(f"[INFO] URL funcionando: {log_url}")

                # Processa frames do stream
                frame_count = 0
                while not self._stop_event.is_set():
                    ok, frame = cap.read()
                    
                    if not ok or frame is None:
                        print("[AVISO] Falha ao ler frame, reconectando...")
                        break
                    
                    frame_count += 1
                    last_frame = frame.copy()

                    # TODO: Substituir pela sua detecção real
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

                    match_servidor, match_score = self._buscar_autorizado(
                        placa_lida=consolidado.valor,
                        tolerancia=config.tolerancia_match_percentual,
                    )

                    evento = EventoLeitura.objects.create(
                        placa_lida=consolidado.valor,
                        placa_normalizada=normalizar_placa(consolidado.valor),
                        confianca_ocr=consolidado.confianca,
                        score_match_percentual=match_score,
                        status=(
                            EventoLeitura.Status.AUTORIZADO
                            if match_servidor
                            else EventoLeitura.Status.DESCONHECIDO
                        ),
                        nome_servidor=match_servidor,
                    )

                    async_to_sync(channel_layer.group_send)(
                        "placas_status",
                        {
                            "type": "placa.status",
                            "payload": {
                                "status": evento.status,
                                "placa": evento.placa_lida,
                                "score": evento.score_match_percentual,
                                "servidor": evento.nome_servidor,
                                "timestamp": timezone.localtime(evento.criado_em).isoformat(),
                            },
                        },
                    )

                    time.sleep(config.intervalo_frames_ms / 1000)

            except Exception as e:
                print(f"[ERRO] Exceção: {str(e)[:100]}")
                consecutive_failures += 1
            
            finally:
                if cap:
                    cap.release()
            
            # Se muitas falhas consecutivas, aguarda mais antes de tentar novamente
            if consecutive_failures >= max_consecutive_failures:
                print(f"[AVISO] Muitas falhas ({consecutive_failures}). Aguardando 5s antes de tentar novamente...")
                time.sleep(5)
                consecutive_failures = 0
            else:
                time.sleep(2)

    def _generate_rtsp_url_variations(self, original_url: str) -> list:
        """Gera variações de URLs RTSP para testar com câmeras Yoosee"""
        variations = [original_url]  # Começa com a URL original
        
        try:
            # Parse da URL
            if "://" not in original_url:
                return variations
            
            # Extrai componentes
            protocol, rest = original_url.split("://", 1)
            
            if "@" in rest:
                creds, host_path = rest.split("@", 1)
            else:
                return variations
            
            # Parse host and path
            if "/" in host_path:
                host_port, path = host_path.split("/", 1)
                path = "/" + path
            else:
                host_port = host_path
                path = ""
            
            # Extract IP and port
            if ":" in host_port:
                host, port = host_port.rsplit(":", 1)
            else:
                host = host_port
                port = "554"
            
            # APENAS as variações mais prováveis para Yoosee (reduz de 14 para 5)
            common_paths = [
                "/stream1",           # Stream 1 - comum em Yoosee
                "/h264/ch1",          # H.264 channel 1
                "/h264/ch1/main",     # H.264 channel 1 main stream
                "",                   # Root
            ]
            
            # Gera URLs com variações
            for alt_path in common_paths:
                new_url = f"{protocol}://{creds}@{host}:{port}{alt_path}"
                if new_url != original_url and new_url not in variations:
                    variations.append(new_url)
            
            print(f"[DEBUG] {len(variations)} URLs geradas para teste")
            return variations
        except Exception as e:
            print(f"[ERRO] Erro ao gerar variações de URL: {e}")
            return [original_url]

    def _coletar_tentativas_ocr(self, frame, tentativas: int, intervalo_ms: int) -> Iterable[OCRResult]:
        resultados = []

        for _ in range(tentativas):
            # TODO: Integrar EasyOCR aqui
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

        amostras = [
            r for r in resultados
            if normalizar_placa(r.valor) == placa_vencedora
        ]

        confianca_media = sum(r.confianca for r in amostras) / len(amostras)

        return OCRResult(valor=placa_vencedora, confianca=confianca_media)

    def _buscar_autorizado(self, placa_lida: str, tolerancia: int):
        placa_lida = normalizar_placa(placa_lida)
        candidatos = load_authorized_vehicles("servidores.csv")

        melhor_servidor = None
        melhor_score = 0.0

        for placa_auth, servidor in candidatos.items():
            score = similaridade_percentual(placa_lida, placa_auth)

            if score > melhor_score:
                melhor_servidor = servidor
                melhor_score = score

        if melhor_servidor and melhor_score >= tolerancia:
            return melhor_servidor, melhor_score

        return None, 0.0


worker = RecognitionWorker()


async def start_worker_async() -> None:
    await asyncio.to_thread(worker.start)