"""Soul Trapper Windows: keyboard and screen-reader front end."""
import os,sys,time,traceback,copy,json
import host
os.environ['PYGAME_HIDE_SUPPORT_PROMPT']='1'
import pygame
from pathlib import Path
from engine import Engine
from audio import Audio
from speech import Speech
from saves import Saves
from crash_report import write_error
ROOT=Path(__file__).resolve().parent
class Game:
 def __init__(self,smoke=False):
  pygame.mixer.pre_init(44100,-16,2,2048);pygame.init();pygame.display.set_mode((800,600));pygame.display.set_caption('Soul Trapper 1.1')
  pygame.display.get_surface().fill('white');pygame.display.flip()
  self.audio=Audio(ROOT);self.speech=Speech(ROOT,silent=smoke);self.saves=Saves(ROOT/'test-save' if smoke else None)
  self.engine=None;self.mode='menu';self.index=0;self.items=[];self.running=True;self.dialog_index=0;self.last_topics=();self.last_save=0;self.held=set();self.accum=0.;self.error=False
  self.read_settings();self.menu()
 def say(self,text):self.speech.say(text)
 def click(self):
  self.audio.load("ui-click","SFX_BUTTON_CLICK.caf");self.audio.start("ui-click")
 def read_settings(self):
  self.settings={'volume':1.,'tutorials':True}
  try:
   data=json.loads((self.saves.root/'settings.json').read_text(encoding='utf8'))
   self.settings['volume']=max(0.,min(1.,float(data.get('volume',1.))))
   self.settings['tutorials']=bool(data.get('tutorials',True))
  except (OSError,ValueError,TypeError):pass
  self.audio.master=self.settings['volume']
 def write_settings(self):
  self.settings['volume']=self.audio.master
  p=self.saves.root/'settings.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(self.settings),encoding='utf8');os.replace(tmp,p)
 def set_volume(self,amount):
  self.audio.master=max(0.,min(1.,round(amount,2)))
  for q in set(self.audio.channels)|set(self.audio.effect_defs):self.audio.volume(q,self.audio.volumes.get(q,1.))
  self.write_settings()
 def repeat_volume_keys(self,dt):
  keys=self.held & {pygame.K_LEFT,pygame.K_RIGHT}
  key=next(iter(keys)) if self.mode=='settings' and self.index==0 and len(keys)==1 else None
  if key!=getattr(self,'volume_repeat_key',None):
   self.volume_repeat_key=key;self.volume_repeat_delay=.4;return
  if key is None:return
  self.volume_repeat_delay-=dt
  if self.volume_repeat_delay<=0:
   self.volume_repeat_delay=.1
   if (key==pygame.K_LEFT and self.audio.master>0) or (key==pygame.K_RIGHT and self.audio.master<1):self.key(pygame.key.name(key))
 def settings_items(self):
  return [f"Master volume: {round(self.audio.master*100)} percent.", 'PC tutorials: '+('checked' if self.settings['tutorials'] else 'unchecked')+'. Press Enter or Space to toggle.', 'Back']
 def open_settings(self):
  self.mode='settings';self.index=0;self.items=self.settings_items();self.say('Settings. '+self.items[0])
 def maybe_tutorial(self):
  if not self.settings['tutorials']:return False
  e=self.engine;kind=None
  if e.chapter==1 and e.get('SceneIndex')==101:kind='navigation'
  elif e.string(e.get('ReleaseButtonImage')&0xffffffff)=='INHALE_EXHALE_BUTTN.png' and e.get('ReleaseSceneIndex')!=-1:kind='breathing'
  elif e.string(e.get('ActionButtonImage')&0xffffffff)=='HEART_RATE_BTTN.png' and e.get('ActionSceneIndex')!=-1:kind='heartbeat'
  if not kind or kind in self.chapter_tutorials:return False
  texts={
   'navigation':"Use the arrow keys to interact with objects around Kane. He tells you what is in each direction, so keep this in mind when he speaks. Wait a few seconds, or press Tab, to repeat Kane's directions. The arrow keys work for most things. If you are unsure how to continue, try the arrow keys. Press F1 at any time to get contextual help for what you are doing.",
   'heartbeat':"You're entering the trap. Press Down on the heartbeats to attempt entry. Keep doing this until you enter.",
   'breathing':"This time, you also need to breathe. Press Up on the inhale and again on the exhale. Make sure to also press Down during the heartbeats."}
  if kind=='breathing' and 'heartbeat' not in self.chapter_tutorials:texts[kind]=texts['heartbeat']+' '+texts[kind]
  self.tutorial_kind=kind;self.tutorial_text=texts[kind]+' Press Enter to continue. Press Tab to repeat these instructions.'
  self.audio.pause();self.mode='tutorial';self.accum=0;self.say(self.tutorial_text);return True
 def finish_tutorial(self):
  self.chapter_tutorials.add(self.tutorial_kind)
  if self.tutorial_kind=='breathing':self.chapter_tutorials.add('heartbeat')
  self.speech.stop();self.mode='play';self.accum=0;self.audio.resume()
 def help_items(self):
  if self.mode=='settings':return ['Up and Down: browse settings.', 'Hold Left or Right: adjust master volume.', 'Home and End: set master volume to zero or 100 percent while on the volume setting.', 'Enter or Space: toggle PC tutorials.', 'Escape: return to the main menu.']
  if self.mode=='credits':return ['Up and Down: browse the original game credits.', 'Enter: repeat the current credit.', 'Escape: return to the main menu.', 'Page Up and Page Down: adjust game volume.']
  if self.mode not in ('play','pause','confirm') or not self.engine:
   return ['Up and Down: move between menu items.', 'Enter: select the highlighted item.', 'Escape: return to the previous menu.', 'Page Up and Page Down: adjust game volume.']
  e=self.engine;options=e.options(include_disabled=True);keys={key for key,_,_ in options};items=[]
  if any(key.isdigit() for key in keys):
   items+=['Up and Down: move between conversation choices.', 'Enter: select the highlighted conversation choice.']
  elif e.chapter==23 and e.string(e.get('ActionButtonImage')&0xffffffff)=='SHELLGAME_BUTTON.png':
   items+=['Bar game controls: Left selects left, Right selects right, and Up selects the center. Listen to the game to decide which to use.']
  elif e.string(e.get('ActionButtonImage')&0xffffffff)=='HEART_RATE_BTTN.png':
   items+=['Heartbeat controls: Down presses the heartbeat button. Up is the breathing button when available. Listen for the rhythm.']
  elif e.organ:
   items+=['Organ controls: number keys 1 through 5 play the notes from lowest to highest. 1 is the lowest and 5 is the highest.']
  elif e.chapter==4 and e.string(e.get('ActionButtonImage')&0xffffffff)=='UNLOCK_SAFE_BTTN.png':
   items+=['Safe controls: Left and Right turn the dial. Up attempts to open the safe. Tab repeats the combination instructions.']
  elif e.chapter==4 and e.string(e.get('ActionButtonImage')&0xffffffff)=='GET_CRYSTAL_BTTN.png':
   items+=['Crystal box controls: Left and Right browse the crystals. Up selects the crystal. Tab repeats the sound of the crystal you need to find.']
  elif e.chapter in (4,7,15) and e.string(e.get('ActionButtonImage')&0xffffffff)=='FREQUENCY_BTTN.png':
   items+=['IVR controls: Left and Right adjust the frequency. Up locks in the frequency.']
  elif e.chapter in (5,17) and e.string(e.get('ActionButtonImage')&0xffffffff)=='MALLET_BTTN.png':
   items+=['Up: swing the hammer at the strongman attraction.' if e.chapter==5 else 'Up: swing the hammer to smash the mirrors.']
  elif any(e.string(e.get(f)&0xffffffff)=='LEVER_BTTN.png' for f in ('ActionButtonImage','ReleaseButtonImage')) and 'space' in keys:
   items+=['Space: pull the lever to release the soul.']
  elif e.chapter==13 and e.string(e.get('ReleaseButtonImage')&0xffffffff)=='SHOVEL_BTTN.png' and 'down' in keys:
   items+=['Metal detector controls: Up walks forward, Left and Right turn, and Down digs at your current position.', 'The detector sound follows the target relative to your facing direction. Beeps become faster as you approach.']
  elif e.string(e.get('ActionButtonImage')&0xffffffff)=='SWORD_CENTER_BTTN.png':
   items+=['Sword controls: Left blocks left, Right blocks right, and Up blocks the center. Listen to the attack to decide which to use.']
  elif e.chapter==9 and e.get('SceneIndex')==11:
   items+=['Up: fire the soul trap when it is ready.']
  elif e.trap_controls():
   items+=['Soul trap controls: Left and Right aim, and Up fires when the trap is ready.']
  elif any(e.string(e.get(f)&0xffffffff)=='FIRE_BTTN.png' for f in ('ActionButtonImage','ReleaseButtonImage')) and 'up' in keys:
   items+=['Up: fire the soul trap when it is ready.']
  else:
   directions=[key.title() for key in ('up','down','left','right') if key in keys]
   if directions:items.append('Direction controls: '+', '.join(directions)+'. Each arrow selects its direction immediately, without pressing Enter.')
   if 'space' in keys:items.append('Space: press the current action or center button.')
   if 'enter' in keys:items.append('Enter: press the current release button.')
  if not items:items.append('Listen to the current scene. Available controls appear here when the scene is ready for input.')
  if 'space' not in keys and not any(k=='up' and m=='ActionButtonAction:' for k,_,m in options):items.append('Space: skip previously heard dialogue or narration when available.')
  items+=['Tab: repeat the available instructions for your current situation.', 'L: read the chapter and location.', 'Escape: open the pause menu during play.', 'F5: save the current story checkpoint, replacing your previous saved checkpoint.', 'F9: review and load your saved checkpoint after confirmation.', 'Page Up and Page Down: adjust game volume.']
  return items
 def open_help(self):
  entries=self.help_items();self.help_return=(self.mode,self.items,self.index)
  if self.mode=='play':self.audio.pause()
  self.mode='help';self.items=entries;self.index=0
  self.say('Help. Up and Down to browse. Escape to return. '+self.items[0])
 def close_help(self):
  self.mode,self.items,self.index=self.help_return;self.accum=0
  self.speech.stop()
  if self.mode=='play':self.audio.resume()
  elif self.items:self.say(self.items[self.index])
 def menu(self,keep_music=False):
  self.mode='menu';self.index=0;self.items=(['Continue'] if self.saves.exists('continue') else [])+['New game','Chapter select','Settings','Credits','Exit']
  if not keep_music:
   self.audio.stop_all();self.audio.load('menu','C01_Title.caf');self.audio.start('menu',True)
  self.say('Soul Trapper. '+self.items[self.index])
 def credits(self,completed=False):
  self.audio.stop_all();self.audio.load('credits','END_CREDITS_MUS_v1.caf');self.audio.volume('credits',.75);self.audio.start('credits')
  self.mode='credits';self.index=0;self.items=json.loads((ROOT/'data/credits.json').read_text(encoding='utf8'))
  self.say(('The end. You have completed Soul Trapper. ' if completed else '')+'Credits. Up and Down to browse. Escape returns to the main menu. '+self.items[0])
 def start(self,ch=1,state=None,replay=False):
  self.chapter_tutorials=set()
  self.audio.stop('menu');self.audio.stop_all();pygame.mixer.stop();self.audio.channels.clear();self.audio.loaded.clear();self.audio.volumes.clear()
  self.engine=Engine(self.audio,ch);self.mode='play';self.error=False;self.dialog_index=0;self.last_topics=();self.accum=0
  self.engine.chapter_start=copy.deepcopy(self.engine.snapshot())
  if state:
   self.engine.restore(state)
  self.engine.replay_chapter=self.engine.replay_chapter or replay
  self.engine.checkpoint=copy.deepcopy(self.engine.snapshot())
  self.safe_checkpoint=self.engine.safe_active()
  self.engine.checkpoint_requested=not bool(state)
  self.last_save=self.engine.now
  self.last_navigation_hint=''
  self.say(self.engine.title+('. Resuming at '+self.engine.location if state else ''))
  if not state:self.saves.write(self.engine,'chapter-start');self.saves.write(self.engine)
 def pause(self):
  if self.mode=='replay':self.end_replay()
  if self.mode!='play':return
  self.audio.pause();self.mode='pause';self.items=['Resume','Save checkpoint','Load checkpoint','Restart chapter','Main menu','Exit'];self.index=0;self.say('Paused. Resume')
 def resume(self):self.mode='play';self.audio.resume();self.say('Resumed.');self.accum=0
 def save(self,manual=False,background=False):
  if self.engine and not self.error and (self.mode in ('play','pause','confirm','replay','tutorial') or (self.mode=='help' and self.help_return[0] in ('play','pause','confirm'))):
   self.saves.write(self.engine,'manual' if manual else 'continue',background=background)
   if manual:
    state=self.saves.read('manual');self.say('Checkpoint saved. '+self.checkpoint_label(state)+'. This replaces your previous saved checkpoint.')
 def checkpoint_label(self,state):
  title=state.get('title') or 'Chapter '+str(state['chapter'])
  location=state.get('location')
  return title+('. '+location if location else '')
 def confirm_checkpoint_save(self):
  if self.mode=='play':self.pause()
  def commit():
   self.save(True)
   self.mode=self.confirm_return;self.items=self.return_items;self.index=0
  self.confirm('Save at the beginning of the current scene? This will replace your previous manual checkpoint. Are you sure?',commit)
 def confirm_checkpoint_load(self):
  if not self.saves.exists('manual'):
   self.say('No saved checkpoint yet. Press F5 during play to create one.');return
  try:destination=self.checkpoint_label(self.saves.read('manual'))
  except Exception:
   self.log_error();self.say('This saved checkpoint could not be read. Your current game and saved files are unchanged.');return
  self.confirm('Loading this checkpoint will return you to '+destination+'. Are you sure?',lambda:self.load('manual'))
 def load(self,slot='continue'):
  if not self.saves.exists(slot):
   if self.mode=='confirm':self.mode=self.confirm_return;self.items=self.return_items;self.index=0
   self.say('No saved checkpoint yet. Press F5 during play to create one.' if slot=='manual' else 'No saved game.');return
  previous=self.__dict__.copy();audio_state=self.audio.snapshot()
  try:
   s=self.saves.read(slot)
   if slot=='continue':
    progress=self.saves.progress()
    self.start(s['chapter'],replay=s.get('replay_chapter',False) or progress.get('unlocked',1)>s['chapter'] or s['chapter'] in progress.get('completed',[]) or s.get('done',False))
    self.engine.heard_tracks=list(s.get('heard_tracks',[]))
    self.saves.write(self.engine)
   else:self.start(s['chapter'],s)
  except Exception:
   self.log_error();self.__dict__.update(previous);self.audio.restore(audio_state)
   if self.mode=='confirm':self.mode=self.confirm_return;self.items=self.return_items;self.index=0
   if self.mode=='pause':self.audio.pause()
   self.say('This save could not be loaded. Your current game and saved files are unchanged. Press Escape to return.')
 def topics(self):return [o for o in self.engine.options() if o[0].isdigit()] if self.engine else []
 def replay_instructions(self):
  tracks=self.engine.instructions()
  if not tracks:
   hint=self.engine.navigation_hint()
   if hint:self.say(hint)
   return
  if self.mode=='replay':self.audio.channel('instructions').stop()
  else:
   self.replay_started=self.audio.now;self.replay_tracks=list(tracks)
   if tracks==self.engine.prompt_tracks:self.replay_tracks=list(set(tracks+self.engine.prompt_variants))
   # Keep ambient/effect queues running. Only isolate the story voice.
   self.replay_paused_fg=self.audio.busy('fg') and self.audio.loaded.get('fg') not in self.replay_tracks
   if self.replay_paused_fg:self.audio.channel('fg').pause()
   else:self.audio.stop('fg')
  self.speech.stop();self.replay_queue=list(tracks);self.mode='replay';self.accum=0
  self.next_instruction()
 def next_instruction(self):
  if not self.replay_queue:self.end_replay();return
  name=self.replay_queue.pop(0)
  channel=self.audio.channel('instructions');channel.set_volume(self.audio.master);channel.play(self.audio.sound(name))
 def end_replay(self):
  self.audio.channel('instructions').stop();self.replay_queue=[];self.mode='play';self.accum=0
  if self.replay_paused_fg:
   if 'fg' in self.audio.active:self.audio.active['fg']['start']+=self.audio.now-self.replay_started
   self.audio.channel('fg').unpause()
  if self.engine.chapter==13 and self.replay_tracks==self.engine.beach_instructions:
   for timer in self.engine.timers.values():
    if timer['valid'] and timer['selector']=='CliffTimeOutFunc':timer['due']=self.engine.now+timer['interval']
  self.engine.manual_repeat={'scene':self.engine.get('SceneIndex'),'tracks':self.replay_tracks,'until':self.engine.now+5.}
 def key(self,key):
  if self.mode=='replay':
   if key=='tab':self.replay_instructions();return
   if key not in ('page up','page down','l'):self.end_replay()
   elif key=='l':self.say(self.engine.title+'. '+self.engine.location);return
  if key in ('page up','page down'):
   self.set_volume(self.audio.master+(.05 if key=='page up' else -.05))
   self.say(f'Volume {round(self.audio.master*100)} percent');return
  if self.mode=='tutorial':
   if key in ('enter','return'):self.finish_tutorial()
   elif key in ('tab','f1','escape'):self.say(self.tutorial_text)
   return
  if self.mode=='settings':
   if key in ('up','down'):
    self.click();self.index=(self.index+(1 if key=='down' else -1))%3
   elif key=='escape' or (self.index==2 and key in ('enter','return')):self.menu(keep_music=True);return
   elif self.index==0 and key in ('left','right','home','end'):self.set_volume(0. if key=='home' else 1. if key=='end' else self.audio.master+(.05 if key=='right' else -.05))
   elif self.index==1 and key in ('enter','return','space','left','right'):self.settings['tutorials']=not self.settings['tutorials'];self.write_settings();self.click()
   elif key=='f1':self.open_help();return
   else:return
   self.items=self.settings_items();self.say(self.items[self.index]);return
  if self.mode=='help':
   if key in ('escape','f1'):self.close_help()
   elif key in ('up','down'):
    self.click();self.index=(self.index+(1 if key=='down' else -1))%len(self.items);self.say(self.items[self.index])
   elif key in ('enter','return'):self.say(self.items[self.index])
   return
  if key=='f1':self.open_help();return
  if self.mode=='credits':
   if key=='escape':self.menu()
   elif key in ('up','down'):
    self.click();self.index=(self.index+(1 if key=='down' else -1))%len(self.items);self.say(self.items[self.index])
   elif key in ('enter','return'):self.say(self.items[self.index])
   return
  if self.mode in ('menu','pause','chapters','confirm'):
   if key in ('up','down'):
    self.click();self.index=(self.index+(1 if key=='down' else -1))%len(self.items);self.say(self.items[self.index]);return
   if key=='escape':
    if self.mode=='pause':self.resume()
    elif self.mode=='confirm':self.mode=self.confirm_return;self.items=self.return_items;self.index=0;self.say(self.items[0])
    elif self.mode=='chapters':self.menu(keep_music=True)
    return
   if key not in ('return','enter'):return
   item=self.items[self.index]
   if self.mode=='confirm':
    action=self.confirm_action
    if item=='Yes':action()
    else:self.mode=self.confirm_return;self.items=self.return_items;self.index=0;self.say(self.items[0])
   elif self.mode=='chapters':
    self.start(self.index+1,replay=True)
   elif item=='Continue':self.load()
   elif item=='Credits':self.credits()
   elif item=='Settings':self.open_settings()
   elif item=='New game':
    if self.saves.exists():self.confirm('Start a new game? This replaces Continue, but keeps your saved checkpoint.',lambda:self.start(1))
    else:self.start(1)
   elif item=='Chapter select':
    self.mode='chapters';self.index=0
    titles=sorted((v for v in self.engine.strings.values() if v.startswith('Chapter ') and ':' in v),key=lambda s:int(s.split(':')[0].split()[1])) if self.engine else self.chapter_titles()
    self.items=titles[:self.saves.progress()['unlocked']];self.say(self.items[0])
   elif item=='Resume':self.resume()
   elif item=='Save checkpoint':self.confirm_checkpoint_save()
   elif item=='Load checkpoint':self.confirm_checkpoint_load()
   elif item=='Restart chapter':self.confirm('Restart this chapter?',lambda:self.start(self.engine.chapter))
   elif item=='Main menu':self.save();self.menu()
   elif item=='Exit':self.save();self.running=False
   self.click();return
  if self.mode=='play':
   if key=='escape':self.pause();return
   if key=='f5':self.confirm_checkpoint_save();return
   if key=='f9':self.pause();self.confirm_checkpoint_load();return
   if key=='l':self.say(self.engine.title+'. '+self.engine.location);return
   if key=='tab':self.replay_instructions();return
   if key=='space' and self.engine.skip_heard():return
   topics=self.topics()
   if key.isdigit():
    if self.engine.organ:self.engine.press(key)
    return
   if topics and key in ('up','down'):
    self.click();self.dialog_index=(self.dialog_index+(1 if key=='down' else -1))%len(topics);self.say(self.engine.topic_label(topics[self.dialog_index]));return
   if topics and key in ('return','enter'):key=topics[self.dialog_index%len(topics)][0]
   if key=='return':key='enter'
   self.engine.press(key)
 def confirm(self,prompt,action):
  self.confirm_return=self.mode;self.return_items=self.items;self.confirm_action=action;self.mode='confirm';self.items=['No','Yes'];self.index=0;self.say(prompt+' No')
 def chapter_titles(self):
  import json
  v=json.loads((ROOT/'data/metadata.json').read_text())['strings'].values()
  return sorted((s for s in v if s.startswith('Chapter ') and ':' in s),key=lambda s:int(s.split(':')[0].split()[1]))
 def update(self,dt):
  self.saves.check_pending()
  if self.mode=='replay':
   self.audio.tick(dt)
   if not self.audio.channel('instructions').get_busy():self.next_instruction()
   return
  if self.mode!='play':return
  self.accum+=dt
  # The original NSTimer runs at 30 Hz (double 0x3fa1111111111111).
  while self.accum>=1/30:
   if self.maybe_tutorial():return
   safe=self.engine.safe_active()
   if safe and not self.safe_checkpoint:
    # Capture the complete original state before the safe introduction runs.
    self.engine.checkpoint=copy.deepcopy(self.engine.snapshot());self.safe_checkpoint=True
   elif not safe and self.safe_checkpoint:
    self.safe_checkpoint=False;self.engine.checkpoint_requested=True
   self.engine.tick(1/30);self.accum-=1/30
   if getattr(self.engine,'checkpoint_pending',False):
    self.engine.checkpoint_pending=False
    if not self.safe_checkpoint and self.engine.checkpoint_requested:
     self.engine.checkpoint_requested=False
     self.engine.checkpoint=copy.deepcopy(self.engine.snapshot())
   if self.engine.done:
    self.saves.write(self.engine)
    ch=self.engine.chapter
    if ch<23:self.start(ch+1,replay=self.engine.replay_chapter and ch+1<=self.saves.progress().get('unlocked',1))
    else:self.credits(completed=True)
    return
  topics=tuple((o[0],o[1]) for o in self.topics())
  if topics and topics!=self.last_topics:
   self.dialog_index=0;self.say('Conversation. Use Up and Down to move between options, and Enter to select. '+self.engine.topic_label(self.topics()[0]))
  self.last_topics=topics
  hint=self.engine.navigation_hint() if not self.engine.instructions() else ''
  if hint and hint!=self.last_navigation_hint:self.say(hint)
  self.last_navigation_hint=hint
  if self.engine.now-self.last_save>=30:
   self.save(background=True);self.last_save=self.engine.now
 def log_error(self):
  e=self.engine
  context='Mode: '+self.mode
  if e:context+='; chapter: '+str(e.chapter)+'; scene: '+str(e.get('SceneIndex'))
  self.error_log_path=write_error(context)
 def run(self,smoke=False):
  clock=pygame.time.Clock();frames=0
  while self.running:
   dt=min(.2,clock.tick(60)/1000)
   try:
    for ev in pygame.event.get():
     if ev.type==pygame.QUIT:self.save();self.running=False
     elif ev.type==pygame.WINDOWFOCUSLOST:self.held.clear();self.pause()
     elif ev.type==pygame.KEYUP:
      self.held.discard(ev.key);self.volume_repeat_key=None
     elif ev.type==pygame.KEYDOWN and ev.key not in self.held:
      self.held.add(ev.key);self.key(pygame.key.name(ev.key))
    self.repeat_volume_keys(dt)
    self.update(dt)
   except Exception:
    self.log_error();self.error=True;self.audio.pause();self.mode='pause';self.items=['Load checkpoint','Restart chapter','Main menu','Exit'];self.index=0
    self.say('Soul Trapper encountered an error. Your previous saves are safe. Please send error.log to the developer. '+str(self.error_log_path or '')+'. Load checkpoint.')
   frames+=1
   if smoke and frames>10:self.running=False
  self.saves.close();self.audio.stop_all();self.speech.stop();pygame.quit()
if __name__=='__main__':
 import unicorn
 print('Soul Trapper 1.1; Python '+sys.version+'; Unicorn '+unicorn.__version__,flush=True)
 if hasattr(sys,'getwindowsversion'):print('Windows runtime version: '+str(sys.getwindowsversion()),flush=True)
 smoke='--smoke-test' in sys.argv
 try:Game(smoke).run(smoke)
 except Exception:
  p=write_error('Startup failure')
  # The launcher shows its own dialog for a crash it can see; --launcher means
  # "someone upstream is handling this", so stay quiet and let them have it.
  if not smoke and '--launcher' not in sys.argv:host.alert('Soul Trapper','Soul Trapper could not start. Please send error.log to the developer. Details: '+str(p))
  raise
