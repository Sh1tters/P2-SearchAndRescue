import requests
import subprocess
import re
import threading
import socket
import logging
from time import sleep, time

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s %(message)s')

BACKEND_URL = 'http://localhost:8000/v1/api/relay' # https://example.com/
ALLOWED_DRONES = ['60-60-1f-5b-4b-ea', '60-60-1f-5b-4b-d8', '60-60-1f-5b-4b-78']

class Relaybox:
    def __init__(self, name, password) -> None:
        self.name = name
        self.password = password
        self.drones = {}
        self.token = None
        
        #----# Heartbeat variables #----#
        self.session = requests.Session()
        self.heartbeat_url = f"{BACKEND_URL}/heartbeat"
        self.heartbeat_timeout = 5.0 # session timeout (seconds)
        self.heartbeat_interval = 2 # loop interval (seconds)
        self.heartbeat_response_time = 0
    
    def connect_to_backend(self) -> None:
        try:
            query = { 'name': self.name, 'password': self.password }
            response = requests.post(f'{BACKEND_URL}/handshake', json=query)
            if response.status_code != 200: # Every HTTPException.
                logging.error(f'Tried connecting to {response.url} | {response.status_code} | Reconnecting in 2 seconds')
                sleep(2)
                self.connect_to_backend()

            token = response.json().get('access_token')
            self.token = token
            logging.info("Connected to backend")
        except Exception:
            logging.error(f'Tried connecting to backend| Reconnecting in 2 seconds')
            sleep(2)
            self.connect_to_backend()
            
    def start(self) -> None:
        logging.info("[THREAD] Scanning for drones...")
        scan_for_drone_thread = threading.Thread(target=self.scan_for_drone, args=(self.filter_scanned_drones,))
        #scan_for_drone_thread.start()
        
        heartbeat_thread = threading.Thread(target=self.heartbeat, args=())
        heartbeat_thread.start()

    def heartbeat(self):
        while True:
            start_time = time()
            try:
                query = {"name": self.name}
                response = self.session.get(self.heartbeat_url, json=query, timeout=self.heartbeat_timeout).json()
            except requests.exceptions.Timeout:
                logging.error("Heartbeat timed out")
                continue
            except requests.exceptions.RequestException:
                logging.error("Heartbeat failed")
                continue
            end_time = time()
            self.heartbeat_response_time = end_time - start_time
            backend_data_status = self.backend_data_up_to_date(response) # Check if backend data is up to date with the data we have here
            logging.info(f'Heartbeat | {backend_data_status} | Response time: {self.heartbeat_response_time:.3f} seconds')
            
            sleep(self.heartbeat_interval)
    
    def backend_data_up_to_date(self, response):
        up_to_date = True
        for drone_name, drone_data in self.drones.items():
            drone = drone_data['objectId']
            backend_drone_data = response.get(self.name, {}).get('drones', {}).get(drone_name, {})
            if backend_drone_data == {'name': drone.name, 'ports': {'video': drone.video_port}}:
                pass
            else:
                up_to_date = False
        for backend_drone_name, backend_drone_data in response.get(self.name, {}).get('drones', {}).items():
            if backend_drone_name not in self.drones:
                up_to_date = False

        if up_to_date:
            return(f"Backend data is up to date for {self.name}")
        else:
            return(f"Backend data is NOT up to date for {self.name}")

    def scan_for_drone(self, callback) -> None: # THREAD!
        while True:
            regex = r"""(192\.168\.137\.[0-9]{0,3}) *([0-9a-z-]*)""" #-Bjørn
            output = str(subprocess.check_output(['arp', '-a']))
            output = output.replace(" \r","")
            scanned_drones = re.findall(regex, output) # [(192.168.137.xxx, 00:00:00:00:00:00), ...] 

            for drone in scanned_drones[:]:
                if drone[1] not in ALLOWED_DRONES:
                    scanned_drones.remove(drone)
            
            for drone in scanned_drones[:]:
                cmd = f"ping -w 100 -n 2 {drone[0]}" 
                pinging = str(subprocess.run(cmd, capture_output=True))
                pinging = pinging.replace(" \r","")

                if "Received = 0" in pinging:
                    scanned_drones.remove(drone)

            callback(scanned_drones)
    
    def filter_scanned_drones(self, scanned_drones):
        # Check for connected drone
        for drone in scanned_drones:
            ips_mapped = []
            for name in self.drones:
                ips_mapped.append(self.drones[name].get('Ip'))

            if drone[0] not in ips_mapped:
                logging.info(f"[CONNECTED] {drone}")
                self.add_drone(drone[0])
        
        # Check for disconnected drone
        drones_object_list = []
        for name in self.drones:
            drones_object_list.append(self.drones[name].get('objectId'))

        for drone in drones_object_list:
            ips_mapped = []
            for x in scanned_drones:
                ips_mapped.append(x[0])
            
            if drone.host not in ips_mapped:
                logging.info(f"[DISCONNECTED] {drone.name} {drone.host}")
                self.delete_drone(drone.name)
                self.disconnected_drone(drone)


    def add_drone(self, host) -> None:
        # find a unique name for the drone
        used_names = []
        for drone in self.drones.keys():
            used_names.append(drone)
        
        for num in range(1, 255):
            if "drone_{:03d}".format(num) not in used_names:
                drone_name = "drone_{:03d}".format(num)
                break

        # create a class for the drone now
        drone = Drone(name=drone_name, parent=self.name, host=host)
        self.drones[drone_name] = { "Ip": host, "objectId": drone }

        drone_thread = threading.Thread(target=drone.start, args=())
        drone_thread.start()

    def delete_drone(self, name) -> None:
        object = self.drones[name].get('objectId')
        del object
        self.drones.pop(name)

    def disconnected_drone(self, drone: object) -> None:
        query = { 'name': drone.name, 'parent': drone.parent }
        response = requests.post(f'{BACKEND_URL}/drone/disconnected', json=query)
        if response.status_code != 200: # Every HTTPException
            logging.error(f'Error: {response.url} | {response.status_code} | Retrying in 2 seconds')
            sleep(2)
            self.disconnected_drone(drone)


class Drone:
    def __init__(self, name, parent, host) -> None:
        self.name = name
        self.parent = parent
        self.host = host
        self.default_drone_port = 8889 
        self.video_port = None #NOTE: video_port for relay -> backend
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.default_buffer_size = 2048

    
    def start(self):
        logging.info(f"Starting {self.name} on {self.parent}")
        logging.debug(f"[{self.name}] Getting available video ports from backend...")
        self.get_video_port()
        logging.debug(f'Received video port {self.video_port}')

        logging.debug(f"[{self.name}] Entering SDK mode...")
        #self.set_drone_sdk()
        logging.debug('Entered SDK mode')

        logging.debug(f"[{self.name}] Telling drone to use port {self.video_port} for streamon...")
        #self.set_drone_streamon_port()
        logging.debug(f'Set {self.name} video port to {self.video_port}')

        logging.debug(f"[{self.name}] Enabling streamon...")
        #self.enable_streamon()
        logging.debug(f"Enabled streamon for {self.name}")

        logging.debug(f"[{self.name}] Starting video thread...")
        #self.video_thread()
        logging.debug(f"[{self.name}] Starting status thread...")
        #self.status_thread()
        logging.debug(f"[{self.name}] Starting rc thread...")
        #self.rc_thread()

    def status_thread(self):
        # Ask drone for status [battery, yaw, altitude...]
        # Send collected status to API
        ...

    def rc_thread(self):
        # Ask API for rc cmds on this drone using drone name and relay name
        # Send collected rc cmds to drone
        ...

    def video_thread(self):
        while True:
            video_feed = self.socket.recvfrom(self.default_buffer_size)

            # Do something with the video feed

    def get_video_port(self):
        query = { 'name': self.name, 'parent': self.parent }
        response = requests.get(f'{BACKEND_URL}/new_drone', json=query)
        
        if response.status_code != 200: # Every HTTPException
            logging.error(f"Error trying to get available port from URL [{response.url}] with status code {response.status_code}")
            self.get_video_port()
        
        port = response.json().get('video_port')
        self.video_port = port

    def set_drone_sdk(self):
        self.send_control_command(self.socket, f"command")

    def set_drone_streamon_port(self):
        self.socket.bind(('0.0.0.0', self.video_port))
        self.send_control_command(self.socket, f"port {self.default_drone_port} {self.video_port}", self.default_buffer_size)

    def enable_streamon(self):
        self.send_control_command(self.socket, "streamon")
        
    def send_control_command(self, socket: socket, command: str, buffer_size: int) -> str:
        try:
            socket.sendto(bytes(command, 'utf-8'), (self.host, self.default_drone_port))
            res = socket.recvfrom(buffer_size)
            return res
        except Exception as command_error:
            logging.debug(command_error)
    
if __name__ == '__main__':
    relay = Relaybox("relay_0001", "123")
    relay.connect_to_backend()
    relay.start()

    relay.filter_scanned_drones( [('192.168.1.130', '00:00:00:00:00')] )

    #drone = Drone("drone_01", "relay_0001", "192.168.1.154")
    #drone.start()