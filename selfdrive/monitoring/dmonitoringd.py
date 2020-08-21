#!/usr/bin/env python3
import gc
from cereal import car
from common.realtime import set_realtime_priority
from common.params import Params
import cereal.messaging as messaging
from selfdrive.controls.lib.events import Events
from selfdrive.monitoring.driver_monitor import DriverStatus, MAX_TERMINAL_ALERTS, MAX_TERMINAL_DURATION
from selfdrive.locationd.calibration_helpers import Calibration
from common.realtime import DT_DMON
from common.realtime import sec_since_boot
import time

def dmonitoringd_thread(sm=None, pm=None):
  gc.disable()

  # start the loop
  set_realtime_priority(53)

  params = Params()

  # Pub/Sub Sockets
  if pm is None:
    pm = messaging.PubMaster(['dMonitoringState'])

  if sm is None:
    sm = messaging.SubMaster(['dragonConf', 'driverState', 'liveCalibration', 'carState', 'model'])

  driver_status = DriverStatus()
  is_rhd = params.get("IsRHD")
  if is_rhd is not None:
    driver_status.is_rhd_region = bool(int(is_rhd))
    driver_status.is_rhd_region_checked = True

  sm['liveCalibration'].calStatus = Calibration.INVALID
  sm['carState'].vEgo = 0.
  sm['carState'].cruiseState.enabled = False
  sm['carState'].cruiseState.speed = 0.
  sm['carState'].buttonEvents = []
  sm['carState'].steeringPressed = False
  sm['carState'].gasPressed = False
  sm['carState'].brakePressed = False
  sm['carState'].standstill = True

  cal_rpy = [0, 0, 0]
  v_cruise_last = 0
  driver_engaged = False

  # dp
  sm['dragonConf'].dpDriverMonitor = True
  sm['dragonConf'].dpSteeringMonitor = True
  sm['dragonConf'].dpSteeringMonitorTimer = 70

  # 10Hz <- dmonitoringmodeld
  while True:
    start_time = sec_since_boot()
    sm.update()

    # dp
    if not sm['dragonConf'].dpDriverMonitor:
      driver_status.active_monitoring_mode = False
      driver_status.face_detected = False
      driver_status.threshold_pre = 15. / sm['dragonConf'].dpSteeringMonitorTimer
      driver_status.threshold_prompt = 6. / sm['dragonConf'].dpSteeringMonitorTimer
      driver_status.step_change = DT_DMON / sm['dragonConf'].dpSteeringMonitorTimer
      if not sm['dragonConf'].dpSteeringMonitor:
        driver_status.awareness = 1.
        driver_status.awareness_active = 1.
        driver_status.awareness_passive = 1.
        driver_status.terminal_alert_cnt = 0
        driver_status.terminal_time = 0

    # Handle calibration
    if sm.updated['liveCalibration']:
      if sm['liveCalibration'].calStatus == Calibration.CALIBRATED:
        if len(sm['liveCalibration'].rpyCalib) == 3:
          cal_rpy = sm['liveCalibration'].rpyCalib

    # Get interaction
    if sm.updated['carState']:
      v_cruise = sm['carState'].cruiseState.speed
      driver_engaged = len(sm['carState'].buttonEvents) > 0 or \
                        v_cruise != v_cruise_last or \
                        sm['carState'].steeringPressed or \
                       sm['carState'].gasPressed or \
                       sm['carState'].brakePressed
      if driver_engaged:
        driver_status.update(Events(), True, sm['carState'].cruiseState.enabled, sm['carState'].standstill)
      v_cruise_last = v_cruise

    # Get model meta
    if sm.updated['model']:
      driver_status.set_policy(sm['model'])

    # Get data from dmonitoringmodeld
    if sm.updated['driverState']:
      events = Events()
      if sm['dragonConf'].dpDriverMonitor:
        driver_status.get_pose(sm['driverState'], cal_rpy, sm['carState'].vEgo, sm['carState'].cruiseState.enabled)
      # Block any engage after certain distrations
      if driver_status.terminal_alert_cnt >= MAX_TERMINAL_ALERTS or driver_status.terminal_time >= MAX_TERMINAL_DURATION:
        events.add(car.CarEvent.EventName.tooDistracted)
      # Update events from driver state
      driver_status.update(events, driver_engaged, sm['carState'].cruiseState.enabled, sm['carState'].standstill)

      # dMonitoringState packet
      dat = messaging.new_message('dMonitoringState')
      dat.dMonitoringState = {
        "events": events.to_msg(),
        "faceDetected": driver_status.face_detected,
        "isDistracted": driver_status.driver_distracted,
        "awarenessStatus": driver_status.awareness,
        "isRHD": driver_status.is_rhd_region,
        "rhdChecked": driver_status.is_rhd_region_checked,
        "posePitchOffset": driver_status.pose.pitch_offseter.filtered_stat.mean(),
        "posePitchValidCount": driver_status.pose.pitch_offseter.filtered_stat.n,
        "poseYawOffset": driver_status.pose.yaw_offseter.filtered_stat.mean(),
        "poseYawValidCount": driver_status.pose.yaw_offseter.filtered_stat.n,
        "stepChange": driver_status.step_change,
        "awarenessActive": driver_status.awareness_active,
        "awarenessPassive": driver_status.awareness_passive,
        "isLowStd": driver_status.pose.low_std,
        "hiStdCount": driver_status.hi_stds,
        "isPreview": False,
      }
      pm.send('dMonitoringState', dat)
    diff = sec_since_boot() - start_time
    if not sm['dragonConf'].dpDriverMonitor and diff < 0.1:
      time.sleep(0.1-diff)

def main(sm=None, pm=None):
  dmonitoringd_thread(sm, pm)

if __name__ == '__main__':
  main()
