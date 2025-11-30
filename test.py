import sounddevice as sd
print(sd.query_devices()) 
print(sd.default.device)  # 현재 기본 장치

