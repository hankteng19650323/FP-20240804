import re
import json
import time
from openpilot.system.assistant.mediaconfig import MediaConfig
from openpilot.system.assistant.streamingclient import RevAiStreamingClient
from openpilot.system.assistant.nav_setter import set_dest_from_transcript
from websocket import _exceptions
from threading import Thread, Event
from queue import Queue
from cereal import messaging, log
from openpilot.system.micd import SAMPLE_BUFFER, SAMPLE_RATE

class AssistantWidgetControl:
  def __init__(self, pm=messaging.PubMaster(['speechToText'])):
    self.pm = pm
    self.pm.wait_for_readers_to_update('speechToText', timeout=1)
  def make_msg(self):
    self.pm.wait_for_readers_to_update('speechToText', timeout=1)
    return messaging.new_message('speechToText', valid=True)
  def begin(self):
    msg = self.make_msg()
    msg.speechToText.state = log.SpeechToText.State.begin # Show
    self.pm.send('speechToText', msg)
  def error(self):
    msg = self.make_msg()
    msg.speechToText.state = log.SpeechToText.State.error
    self.pm.send('speechToText', msg)
  def empty(self):
    msg = self.make_msg()
    msg.speechToText.state = log.SpeechToText.State.empty
    self.pm.send('speechToText', msg)
  def set_text(self, text, final=True):
    msg = self.make_msg()
    msg.speechToText.transcript = text
    msg.speechToText.state = log.SpeechToText.State.none if not final else log.SpeechToText.State.final
    self.pm.send('speechToText', msg)

class SpeechToTextProcessor:
  TIMEOUT_DURATION = 10
  RATE = SAMPLE_RATE
  CHUNK = SAMPLE_BUFFER
  BUFFERS_PER_SECOND = SAMPLE_RATE/SAMPLE_BUFFER
  QUEUE_TIME = 10  # Save the first 10 seconds to the queue
  CONNECTION_TIMEOUT = 30

  def __init__(self, access_token, queue_size=BUFFERS_PER_SECOND*QUEUE_TIME):
    self.reva_access_token = access_token
    self.audio_queue = Queue(maxsize=int(queue_size))
    self.stop_thread = Event()
    self.pm = messaging.PubMaster(['speechToText'])
    self.sm = messaging.SubMaster(['microphoneRaw'])
    self.awc = AssistantWidgetControl(self.pm)
    media_config = MediaConfig('audio/x-raw', 'interleaved', 16000, 'S16LE', 1)
    try:
      self.streamclient = RevAiStreamingClient(self.reva_access_token, media_config)
    except Exception as e:
      print(f"An error occurred: {e}")
    self.error = False

  def microphone_data_collector(self):
    """Thread function for collecting microphone data."""
    while not self.stop_thread.is_set():
      self.sm.update(0)
      if self.sm.updated['microphoneRaw']:
        data = self.sm['microphoneRaw'].rawSample
        if not self.audio_queue.full():
          self.audio_queue.put(data)
        else:
          print("Queue is full, stopping")
          self.stop_thread.set()
          self.awc.error()

  def microphone_stream(self):
    """Generator that yields audio chunks from the queue."""
    loop_count = 0
    start_time = time.time()
    while True:
      if loop_count >= self.audio_queue.maxsize or time.time() - start_time > self.CONNECTION_TIMEOUT:
        print(f'Timeout reached. {loop_count=}, {time.time()-start_time=}')
        break
      elif self.stop_thread.is_set():
        break
      elif not self.audio_queue.empty():
        data = self.audio_queue.get(block=True)
        loop_count += 1
        yield data
      else:
        time.sleep(.1)

  def listen_print_loop(self, response_gen, final_transcript):
    try:
      for response in response_gen:
        data = json.loads(response)
        if data['type'] == 'final':
          # Extract and concatenate the final transcript then send it
          final_transcript = ' '.join([element['value'] for element in data['elements'] if element['type'] == 'text'])
          print(final_transcript)
          self.awc.set_text(re.sub(r'<[^>]*>', '', final_transcript), final=False)
        else:
          # Handle partial transcripts (optional)
          partial_transcript = ' '.join([element['value'] for element in data['elements'] if element['type'] == 'text'])
          print(partial_transcript)
          self.awc.set_text(re.sub(r'<[^>]*>', '', partial_transcript), final=False)
    except Exception as e:
      print(f"An error occurred: {e}")
      self.error=True
    return re.sub(r'<[^>]*>', '', final_transcript) # remove atmospherics. ex: <laugh>

  def run(self):
    self.audio_queue.queue.clear()
    collector_thread = Thread(target=self.microphone_data_collector)
    final_transcript = ""
    self.error = False
    collector_thread.start()
    self.awc.begin()
    try:
      response_gen = self.streamclient.start(self.microphone_stream(),
                                             remove_disfluencies=True, # remove umms
                                             filter_profanity=True, # brand integridity or something
                                             detailed_partials=False, # don't need time stamps
                                             max_segment_duration_seconds=5,
                                            )
      final_transcript = self.listen_print_loop(response_gen, final_transcript)
    except _exceptions.WebSocketAddressException as e:
      print(f"WebSocketAddressException: Address unreachable. {e}")
      self.error = True
    except Exception as e:
      print(f"An error occurred: {e}")
      self.error = True
    finally:
      print("Waiting for collector_thread to join...")
      self.stop_thread.set() # end the stream
      collector_thread.join()
      self.stop_thread.clear()
      print("collector_thread joined")
      if not self.error:
        dest = set_dest_from_transcript(final_transcript)
        if dest:
          self.awc.set_text(f"Navigating to {dest['place_name']}", final=True)
        else:
          self.awc.set_text("Place not found", final=True)
      else:
        self.awc.error()

