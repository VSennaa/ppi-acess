import os
import cv2
import numpy as np
import easyocr
import re
from datetime import datetime, timedelta
import csv
import json
import Levenshtein

def load_config(config_path='config.json'):
    default_config = {
        "CAMERA_RTSP_URL": "rtsp://usuario:senha@ip_da_camera/stream",
        "MODO_OPERACAO": {"DEBUG_MODE": True, "USE_ADVANCED_PLATE_FINDER": False},
        "PARAMETROS_AUTORIZACAO": {"ARQUIVO_SERVIDORES_CSV": "servidores.csv", "TOLERANCIA_MATCH": 1},
        "PARAMETROS_PERFORMANCE": {"FRAME_SKIP": 5, "FRAME_WIDTH": 800},
        "PARAMETROS_DETECCAO": {"CONFIDENCE_THRESHOLD": 0.5, "OCR_CONFIDENCE_THRESHOLD": 0.4, "COOLDOWN_SEGUNDOS": 10},
        "PARAMETROS_DETECTOR_AVANCADO": {"GAUSSIAN_BLUR_KERNEL": [5, 5], "MIN_ASPECT_RATIO": 2.0, "MAX_ASPECT_RATIO": 4.5, "MIN_PLATE_WIDTH": 60, "MIN_PLATE_HEIGHT": 15}
    }
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            print(f"[INFO] Arquivo '{config_path}' carregado com sucesso.")
            return json.load(f)
    except FileNotFoundError:
        print(f"[AVISO] Arquivo '{config_path}' não encontrado. Criando um arquivo padrão.")
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, indent=4)
        print(f"[ERRO] Por favor, edite o arquivo '{config_path}' com suas configurações e rode o script novamente.")
        return None
    except json.JSONDecodeError:
        print(f"[ERRO] O arquivo '{config_path}' contém um erro de sintaxe JSON e não pôde ser lido.")
        return None

def load_authorized_vehicles(csv_path):
    if not os.path.exists(csv_path):
        print(f"[ERRO] Arquivo de servidores '{csv_path}' não encontrado.")
        return {}
    vehicles = {}
    try:
        # A MUDANÇA ESTÁ AQUI: adicionamos o delimitador
        with open(csv_path, mode='r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f, delimiter=';') 
            for row in reader:
                # O resto da função continua igual
                if 'PLACA' in row and 'SERVIDOR' in row:
                    plate = re.sub(r'[^A-Z0-9]', '', row['PLACA'].upper())
                    server_info = row['SERVIDOR'].strip()
                    if plate:
                        vehicles[plate] = server_info
        print(f"[INFO] {len(vehicles)} veículos autorizados carregados de '{csv_path}'.")
    except Exception as e:
        print(f"[ERRO] Falha ao ler o arquivo CSV: {e}")
        return {}
    return vehicles

def find_match_in_whitelist(detected_plate, authorized_vehicles, tolerance):
    for authorized_plate, server_info in authorized_vehicles.items():
        distance = Levenshtein.distance(detected_plate, authorized_plate)
        if distance <= tolerance:
            return {"servidor": server_info, "placa_autorizada": authorized_plate}
    return None

def trigger_release_action(detected_plate, match_info):
    print("----------------------------------------------------")
    print(f"[LIBERADO] Veículo autorizado detectado!")
    print(f"  > Servidor: {match_info['servidor']}")
    print(f"  > Placa na Lista: {match_info['placa_autorizada']}")
    print(f"  > Placa Detectada: {detected_plate}")
    print("----------------------------------------------------")

def is_valid_plate_format(text):
    cleaned_text = re.sub(r'[^A-Z0-9]', '', text.upper())
    if len(cleaned_text) != 7: return False, None
    if re.match(r"^[A-Z]{3}[0-9]{4}$", cleaned_text) or re.match(r"^[A-Z]{3}[0-9][A-Z][0-9]{2}$", cleaned_text):
        return True, cleaned_text
    return False, None

def find_plate_candidates_advanced(vehicle_img, params):
    gray = cv2.cvtColor(vehicle_img, cv2.COLOR_BGR2GRAY)
    blur_kernel = tuple(params["GAUSSIAN_BLUR_KERNEL"])
    blurred = cv2.GaussianBlur(gray, blur_kernel, 0)
    edges = cv2.Canny(blurred, 50, 200)
    if config["MODO_OPERACAO"]["DEBUG_MODE"]: cv2.imshow("Debug - Edges", edges)
    contours, _ = cv2.findContours(edges.copy(), cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:10]:
        (x, y, w, h) = cv2.boundingRect(c)
        aspect_ratio = w / float(h)
        if params["MIN_ASPECT_RATIO"] < aspect_ratio < params["MAX_ASPECT_RATIO"] and w > params["MIN_PLATE_WIDTH"] and h > params["MIN_PLATE_HEIGHT"]:
            return vehicle_img[y:y+h, x:x+w]
    return None

def save_plate_to_csv(image_name, plate_text, timestamp):
    with open(os.path.join("placas", "placas.csv"), mode='a', newline='', encoding='utf-8') as f:
        csv.writer(f).writerow([image_name, plate_text, timestamp])

config = load_config()
if config is None: exit()

authorized_vehicles = load_authorized_vehicles(config["PARAMETROS_AUTORIZACAO"]["ARQUIVO_SERVIDORES_CSV"])
PROTOTXT, MODEL = "deploy.prototxt", "mobilenet_iter_73000.caffemodel"
CAPTURAS_DIR = "capturas"
placas_recentes = {}

print("[INFO] Carregando modelo de detecção de objetos...")
net = cv2.dnn.readNetFromCaffe(PROTOTXT, MODEL)
CLASSES = ["background","aeroplane","bicycle","bird","boat","bottle","bus","car","cat","chair","cow","diningtable","dog","horse","motorbike","person","pottedplant","sheep","sofa","train","tvmonitor"]

print("[INFO] Inicializando EasyOCR...")
reader = easyocr.Reader(['pt'], gpu=False)

cap = cv2.VideoCapture(config["CAMERA_RTSP_URL"])
if not cap.isOpened():
    print(f"[ERRO] Não foi possível abrir o stream da câmera: {config['CAMERA_RTSP_URL']}")
    exit()

print("[INFO] Processando vídeo... Pressione 'q' para sair.")
frame_count = 0

while True:
    ret, frame = cap.read()
    if not ret: print("[AVISO] Stream finalizado."); break
    
    frame_count += 1
    if frame_count % config["PARAMETROS_PERFORMANCE"]["FRAME_SKIP"] != 0: continue

    h, w = frame.shape[:2]
    r = config["PARAMETROS_PERFORMANCE"]["FRAME_WIDTH"] / float(w)
    frame = cv2.resize(frame, (config["PARAMETROS_PERFORMANCE"]["FRAME_WIDTH"], int(h * r)), interpolation=cv2.INTER_AREA)
    (h, w) = frame.shape[:2]

    blob = cv2.dnn.blobFromImage(frame, 0.007843, (300, 300), 127.5)
    net.setInput(blob)
    detections = net.forward()

    agora = datetime.now()
    cooldown = config["PARAMETROS_DETECCAO"]["COOLDOWN_SEGUNDOS"]
    placas_recentes = {p: t for p, t in placas_recentes.items() if (agora - t) <= timedelta(seconds=cooldown)}

    for i in range(detections.shape[2]):
        confidence, idx = detections[0, 0, i, 2], int(detections[0, 0, i, 1])
        if confidence > config["PARAMETROS_DETECCAO"]["CONFIDENCE_THRESHOLD"] and CLASSES[idx] in ["car", "bus", "motorbike"]:
            box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
            (startX, startY, endX, endY) = box.astype("int")
            vehicle_img = frame[startY:endY, startX:endX]
            if vehicle_img.size == 0: continue

            if config["MODO_OPERACAO"]["DEBUG_MODE"]: cv2.imshow("Debug - Vehicle Crop", vehicle_img)

            image_to_ocr = find_plate_candidates_advanced(vehicle_img, config["PARAMETROS_DETECTOR_AVANCADO"]) if config["MODO_OPERACAO"]["USE_ADVANCED_PLATE_FINDER"] else vehicle_img
            
            if image_to_ocr is not None:
                if config["MODO_OPERACAO"]["DEBUG_MODE"]: cv2.imshow("Debug - Image Sent to OCR", image_to_ocr)
                ocr_results = reader.readtext(image_to_ocr, allowlist='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')
                for (bbox, text, prob) in ocr_results:
                    if prob > config["PARAMETROS_DETECCAO"]["OCR_CONFIDENCE_THRESHOLD"]:
                        is_valid, plate_text = is_valid_plate_format(text)
                        if is_valid and plate_text not in placas_recentes:
                            placas_recentes[plate_text] = agora
                            timestamp_obj, timestamp_str = datetime.now(), datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            image_name = f"placa_{timestamp_obj.strftime('%Y%m%d_%H%M%S')}.png"
                            cv2.imwrite(os.path.join(CAPTURAS_DIR, image_name), vehicle_img)
                            save_plate_to_csv(image_name, plate_text, timestamp_str)
                            print(f"[SUCESSO] Placa detectada: {plate_text}")

                            match_info = find_match_in_whitelist(plate_text, authorized_vehicles, config["PARAMETROS_AUTORIZACAO"]["TOLERANCIA_MATCH"])
                            if match_info:
                                trigger_release_action(plate_text, match_info)

                            cv2.rectangle(frame, (startX, startY), (endX, endY), (0, 255, 0), 2)
                            cv2.putText(frame, plate_text, (startX, startY - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                            break
    cv2.imshow("Detecção de Placas - Pressione 'q' para sair", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'): break

print("[INFO] Finalizando...")
cap.release()
cv2.destroyAllWindows()
