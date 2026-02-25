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

# 👇 AQUI ESTÁ A CORREÇÃO DO ERRO 👇
# Declaramos a variável globalmente para o views.py conseguir enxergar
last_frame = None

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

    def _try_connect_with_timeout(self, url, timeout_sec: int = 8) -> Optional[cv2.VideoCapture]:
        """Tenta conectar com timeout tratando int (webcam) e str (RTSP)"""
        cap_container = [None]
        
        def connect():
            try:
                if str(url).isdigit():
                    source = int(url)
                    print(f"[DEBUG] Abrindo câmera local: {source}")
                    cap = cv2.VideoCapture(source)
                elif isinstance(url, str) and url.startswith("rtsp"):
                    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
                    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
                else:
                    cap = cv2.VideoCapture(url)
                
                cap_container[0] = cap
            except Exception as e:
                print(f"[ERRO CONEXÃO] Detalhes: {e}")
        
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
            print("[ERRO] Nenhuma configuração ativa encontrada no Banco de Dados.")
            print("[DICA] Acesse o /admin e crie uma configuração com a URL 0 e marque como ativa.")
            return

        raw_source = str(config.rtsp_url).strip()
        is_webcam = raw_source == "0"
        
        if not is_webcam and raw_source.startswith("rtsp"):
            urls_to_try = self._generate_rtsp_url_variations(raw_source)
        else:
            urls_to_try = [0] if is_webcam else [raw_source]

        print(f"[INFO] Iniciando com sources: {urls_to_try}")

        channel_layer = get_channel_layer()
        consecutive_failures = 0
        max_consecutive_failures = 5
        url_index = 0

        while not self._stop_event.is_set():
            cap = None
            current_url = urls_to_try[url_index % len(urls_to_try)]
            url_index += 1
            
            try:
                log_url = current_url
                if isinstance(current_url, str) and '@' in current_url:
                    log_url = "[RTSP PROTEGIDO]"
                
                print(f"[INFO] Tentativa: {log_url}")
                cap = self._try_connect_with_timeout(current_url, timeout_sec=8)

                if cap is None:
                    print(f"[AVISO] Falha ao conectar em {log_url}")
                    consecutive_failures += 1
                    time.sleep(2)
                    continue

                consecutive_failures = 0
                print(f"[INFO] ✓ Conectado com sucesso!")

                while not self._stop_event.is_set():
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        break
                    
                    last_frame = frame.copy()

                    # Simulação de OCR (Onde entrará o EasyOCR depois)
                    # Comentei as tentativas simuladas para focar apenas em exibir a imagem agora
                    # ...
                    
                    time.sleep(0.01)

            except Exception as e:
                print(f"[ERRO] Exceção no loop principal: {str(e)}")
                consecutive_failures += 1
            finally:
                if cap:
                    cap.release()
            
            if consecutive_failures >= max_consecutive_failures:
                print("[AVISO] Muitas falhas. Aguardando 5s...")
                time.sleep(5)
                consecutive_failures = 0

    def _generate_rtsp_url_variations(self, original_url: str) -> list:
        variations = [original_url]
        try:
            if "://" not in original_url: return variations
            return variations
        except:
            return [original_url]

worker = RecognitionWorker()

async def start_worker_async() -> None:
    await asyncio.to_thread(worker.start)