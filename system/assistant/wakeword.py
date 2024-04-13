#!/usr/bin/env python3
import numpy as np
from pathlib import Path
from openpilot.system.assistant.openwakeword import Model
from openpilot.system.assistant.openwakeword.utils import download_models
from openpilot.system.assistant.rev_speechd import SpeechToTextProcessor
from openpilot.common.params import Params
from cereal import messaging
from openpilot.system.micd import SAMPLE_BUFFER, SAMPLE_RATE


class WakeWordListener:
  RATE = 12.5
  PHRASE_MODEL_NAME = "hey_frog_pilot"
  MODEL_DIR = Path(__file__).parent / 'models'
  PHRASE_MODEL_PATH = f'{MODEL_DIR}/{PHRASE_MODEL_NAME}.onnx'
  MEL_MODEL_PATH = f'{MODEL_DIR}/melspectrogram.onnx'
  EMB_MODEL_PATH = f'{MODEL_DIR}/embedding_model.onnx'
  THRESHOLD = .5
  def __init__(self, model_path=PHRASE_MODEL_PATH, threshhold=THRESHOLD):
    self.owwModel = Model(wakeword_models=[model_path], melspec_model_path=self.MEL_MODEL_PATH, embedding_model_path=self.EMB_MODEL_PATH, sr=SAMPLE_RATE)
    self.sm = messaging.SubMaster(['microphoneRaw'])
    
    self.params = Params()
    reva_access_token = "02afQYxGXg_idiFSfieqIr4WE1fpT23ECj_NRFlJzZpLVRD75ft9-fSDy8SoGcd9V4OJz3x-QbPd-2jpGbPTkVtTSaD3A"
    self.sttproc = SpeechToTextProcessor(access_token=reva_access_token)

    self.model_name = model_path.split("/")[-1].split(".onnx")[0]
    self.frame_index = 0
    self.frame_index_last = 0
    self.detected_last = False
    self.threshhold = threshhold

  def update(self):
    self.frame_index = self.sm['microphoneRaw'].frameIndex
    if not (self.frame_index_last == self.frame_index or
            self.frame_index - self.frame_index_last == SAMPLE_BUFFER):
      print(f'skipped {(self.frame_index - self.frame_index_last)//SAMPLE_BUFFER-1} sample(s)') # TODO: Stop it from skipping
    if self.frame_index_last == self.frame_index:
      print("got the same frame")
      return
    self.frame_index_last = self.frame_index
    sample = np.frombuffer(self.sm['microphoneRaw'].rawSample, dtype=np.int16)
    prediction_score = self.owwModel.predict(sample)
    detected = prediction_score[self.model_name] >= self.threshhold
    if detected:
      print("wake word detected")
      self.sttproc.run()
      self.owwModel.reset()
      #self.params.put_bool("WakeWordDetected", True)
    self.detected_last = detected

  def wake_word_runner(self):
    self.sm.update(0)
    if self.sm.updated['microphoneRaw']:
        self.update()

def main():
  download_models([WakeWordListener.PHRASE_MODEL_NAME], WakeWordListener.MODEL_DIR)
  wwl = WakeWordListener()
  while True:
    wwl.wake_word_runner()

if __name__ == "__main__":
  main()
