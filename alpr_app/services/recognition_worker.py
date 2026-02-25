import asyncio
import threading
import time
import os
import csv
import re
from datetime import datetime, timedelta
from typing import Optional

import cv2
import numpy as np
import easyocr

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils import timezone
from difflib import SequenceMatcher

from alpr_app.models import Configuracao, EventoLeitura

last_frame = None

# Constantes do Modelo MobileNet SSD
PROTOTXT = "deploy.prototxt"
MODEL = "mobilenet_iter_73000.caffemodel"
CLASSES = ["background","aeroplane","bicycle","bird","boat","bottle","bus","car","cat","chair","cow","diningtable","dog","horse","motorbike","person","pottedplant","sheep","sofa","train","tvmonitor"]

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

def is_valid_plate_format(text):
    cleaned_text = normalizar_placa(text)
    
    # Procura um bloco de 7 caracteres contínuos no meio da sujeira do OCR
    match = re.search(r'([A-Z0-9]{7})', cleaned_text)
    
    if match:
        plate_candidate = match.group(1)
        
        # --- CORREÇÃO DE OCR INTELIGENTE ---
        corrigido = list(plate_candidate)
        
        # 1. As 3 primeiras posições no Brasil SEMPRE são letras.
        subs_letras = {'0': 'O', '1': 'I', '5': 'S', '8': 'B'}
        for i in range(3):
            if corrigido[i] in subs_letras:
                corrigido[i] = subs_letras[corrigido[i]]
                
        # 2. As posições 3, 5 e 6 SEMPRE são números (Mercosul ou Antiga).
        subs_numeros = {'O': '0', 'I': '1', 'S': '5', 'B': '8', 'G': '6', 'Z': '2', 'A': '4'}
        for i in [3, 5, 6]:
            if corrigido[i] in subs_numeros:
                corrigido[i] = subs_numeros[corrigido[i]]
                
        plate_final = "".join(corrigido)
        return True, plate_final
        
    return False, None

def find_plate_candidates_advanced(vehicle_img):
    gray = cv2.cvtColor(vehicle_img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 200)
    contours, _ = cv2.findContours(edges.copy(), cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:10]:
        (x, y, w, h) = cv2.boundingRect(c)
        aspect_ratio = w / float(h)
        if 2.0 < aspect_ratio < 4.5 and w > 60 and h > 15:
            return vehicle_img[y:y+h, x:x+w]
    return vehicle_img

class RecognitionWorker:
    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.placas_recentes = {} 
        self.net = None
        self.reader = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive(): return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _try_connect_with_timeout(self, url, timeout_sec: int = 8) -> Optional[cv2.VideoCapture]:
        cap_container = [None]
        def connect():
            try:
                if str(url).isdigit():
                    cap = cv2.VideoCapture(int(url))
                elif isinstance(url, str) and url.startswith("rtsp"):
                    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
                    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
                else:
                    cap = cv2.VideoCapture(url)
                cap_container[0] = cap
            except Exception as e:
                print(f"[ERRO CONEXÃO] {e}")
        
        connect_thread = threading.Thread(target=connect, daemon=True)
        connect_thread.start()
        connect_thread.join(timeout=timeout_sec)
        if cap_container[0] and cap_container[0].isOpened():
            return cap_container[0]
        return None

    def _buscar_autorizado(self, placa_lida: str, tolerancia: int):
        candidatos = load_authorized_vehicles()
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

    def _registrar_evento(self, plate_text, prob, config, channel_layer):
        match_servidor, match_score = self._buscar_autorizado(plate_text, config.tolerancia_match_percentual)

        evento = EventoLeitura.objects.create(
            placa_lida=plate_text,
            placa_normalizada=plate_text,
            confianca_ocr=prob,
            score_match_percentual=match_score,
            status=EventoLeitura.Status.AUTORIZADO if match_servidor else EventoLeitura.Status.DESCONHECIDO,
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

    def _enviar_log_ws(self, mensagem: str, channel_layer):
        async_to_sync(channel_layer.group_send)(
            "placas_status",
            {
                "type": "placa.status",
                "payload": {
                    "is_log": True,
                    "mensagem": mensagem,
                    "timestamp": datetime.now().strftime("%H:%M:%S")
                },
            },
        )

    def _run(self) -> None:
        global last_frame
        config = Configuracao.objects.filter(ativo=True).first()
        if not config: return

        print("[INFO] Carregando Rede Neural MobileNet...")
        self.net = cv2.dnn.readNetFromCaffe(PROTOTXT, MODEL)
        
        print("[INFO] Inicializando EasyOCR...")
        self.reader = easyocr.Reader(['pt'], gpu=False) 

        raw_source = str(config.rtsp_url).strip()
        urls_to_try = [0] if raw_source == "0" else [raw_source]
        channel_layer = get_channel_layer()
        frame_skip_counter = 0

        while not self._stop_event.is_set():
            cap = self._try_connect_with_timeout(urls_to_try[0])
            if cap is None:
                time.sleep(2)
                continue
                
            # Força o OpenCV a ignorar buffer interno (útil para RTSP/USB)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            print("[INFO] ✓ Câmera conectada. Iniciando Detecção de Placas!")
            
            # --- O "OLHO": THREAD DE CAPTURA RÁPIDA ---
            # Essa thread só faz uma coisa: pegar a foto mais recente e atualizar a tela.
            # Não pensa, não trava, só trabalha.
            self.capture_active = True
            def grabber_thread_func():
                global last_frame
                while self.capture_active and not self._stop_event.is_set():
                    ok, frame = cap.read()
                    if not ok: break
                    last_frame = frame.copy()

            grabber_thread = threading.Thread(target=grabber_thread_func, daemon=True)
            grabber_thread.start()

            # --- O "CÉREBRO": LOOP DE IA (OCR e MobileNet) ---
            while not self._stop_event.is_set() and grabber_thread.is_alive():
                if last_frame is None:
                    time.sleep(0.01)
                    continue
                
                # O cérebro copia a foto exata do momento e vai trabalhar
                frame_atual = last_frame.copy()
                frame_skip_counter += 1

                if frame_skip_counter % 5 != 0:
                    time.sleep(0.01)
                    continue

                h, w = frame_atual.shape[:2]
                if w == 0 or h == 0: continue
                r = 800.0 / float(w)
                frame_resized = cv2.resize(frame_atual, (800, int(h * r)), interpolation=cv2.INTER_AREA)
                (h_res, w_res) = frame_resized.shape[:2]

                blob = cv2.dnn.blobFromImage(frame_resized, 0.007843, (300, 300), 127.5)
                self.net.setInput(blob)
                detections = self.net.forward()

                agora = datetime.now()
                self.placas_recentes = {p: t for p, t in self.placas_recentes.items() if (agora - t) <= timedelta(seconds=10)}

                for i in range(detections.shape[2]):
                    confidence = detections[0, 0, i, 2]
                    idx = int(detections[0, 0, i, 1])

                    if confidence > 0.5 and CLASSES[idx] in ["car", "bus", "motorbike"]:
                        box = detections[0, 0, i, 3:7] * np.array([w_res, h_res, w_res, h_res])
                        (startX, startY, endX, endY) = box.astype("int")
                        
                        startX, startY = max(0, startX), max(0, startY)
                        endX, endY = min(w_res, endX), min(h_res, endY)
                        
                        vehicle_img = frame_resized[startY:endY, startX:endX]
                        if vehicle_img.size == 0: continue

                        self._enviar_log_ws("Veículo detectado! Processando OCR...", channel_layer)

                        image_to_ocr = find_plate_candidates_advanced(vehicle_img)
                        ocr_results = self.reader.readtext(image_to_ocr, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')
                        
                        for (bbox, text, prob) in ocr_results:
                            if len(text) >= 4:
                                self._enviar_log_ws(f"Texto lido: '{text}' (Conf: {prob:.0%})", channel_layer)
                            
                            if prob > 0.3: # Reduzi a confiança pra ajudar o corretor inteligente
                                is_valid, plate_text = is_valid_plate_format(text)
                                
                                if is_valid and plate_text not in self.placas_recentes:
                                    print(f"[SUCESSO] Placa validada: {plate_text}")
                                    self.placas_recentes[plate_text] = agora
                                    self._registrar_evento(plate_text, prob, config, channel_layer)

            # Se saiu do loop, a câmera caiu. Para o "Olho" e reconecta.
            self.capture_active = False
            grabber_thread.join()
            if cap: cap.release()

worker = RecognitionWorker()

async def start_worker_async() -> None:
    await asyncio.to_thread(worker.start)