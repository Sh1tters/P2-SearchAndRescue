from fastapi import HTTPException, status, APIRouter, Depends
import time

from helper_functions import generate_access_token
from mongodb_handler import get_mongo
from models import DroneModel, RelayHandshakeModel, RelayHeartbeatModel
from relaybox import Relay

relay_router = APIRouter()
active_relays = {}

@relay_router.post("/")
def handle():
    pass

@relay_router.get('/cmd_queue')
def handle(relay: RelayHeartbeatModel): # Relay wants every drone cmd_queue that is linked to him
    if relay.name not in active_relays.keys(): # invalid relay name? or not active?
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Relay not found",
    )

    drones_cmd = {} # { "drone_01": ['up', 'down', 'left'], "drone_02": ['down', 'left']}
    for drone in active_relays[relay.name].drones.values():
        drones_cmd[drone.name] = drone.cmd_queue

    return drones_cmd

@relay_router.get("/heartbeat")
def handle(relay: RelayHeartbeatModel): # Relay should send a heartbeat every 2sec

    # Get utc since 1970
    utc: int = int(time.time())

    # Get specific relay object for the relay who made the heartbeat
    relay = active_relays[relay.name]
    relay.last_heartbeat_received = utc

    print(f"(!) Heartbeat from {relay.name} | timestamp {utc}")
    print(f"(!) Retrieving all data related to {relay.name}")
    
    data = active_relays.get(relay.name)

    return { "message": f"Hello {relay.name}", f"{relay.name}": { "drones": data.drones } }

@relay_router.post("/drone/disconnected")
def handle(drone: DroneModel):
    # Check if drones parent (relay) is not online/exist
    if drone.parent not in active_relays.keys():
        raise HTTPException(
            detail=f"{drone.parent} does not exist or is not online",
            status_code=status.HTTP_400_BAD_REQUEST)
    
    # Check if drone name does not exist in the relay drones list
    if drone.name not in active_relays[drone.parent].drones:
        raise HTTPException(
            detail=f"{drone.name} does not exist in {active_relays[drone.parent].name}",
            status_code=status.HTTP_409_CONFLICT)
    
    # Find that relay object now
    relay = active_relays[drone.parent]

    # Delete the drone object on relay drones
    del relay.drones[drone.name]

    return { "message": f"Deleted {drone.name} on {relay.name}"}


@relay_router.post("/drones")
def handle(relay: RelayHandshakeModel):
    # Check if relay is online/exist
    if relay.name not in active_relays.keys():
        raise HTTPException(
            detail=f"{relay.name} does not exist or is not online",
            status_code=status.HTTP_400_BAD_REQUEST)
    
    # Find that relay object now
    relay = active_relays[relay.name]

    result = {}
    for drone_key in relay.drones.keys():
        drone = relay.drones[drone_key]
        result[drone_key] = { "name": drone.name, "ports": drone.ports }
    
    return result

@relay_router.get("/new_drone")
def handle(drone: DroneModel):
    # Check if drones parent (relay) is not online/exist
    if drone.parent not in active_relays.keys():
        raise HTTPException(
            detail=f"{drone.parent} does not exist or is not online",
            status_code=status.HTTP_400_BAD_REQUEST)
    
    # Check if drone name already exist in the relay drones list
    if drone.name in active_relays[drone.parent].drones:
        raise HTTPException(
            detail=f"{drone.name} already exist or online",
            status_code=status.HTTP_409_CONFLICT)
        
    # Find that relay object now
    relay = active_relays[drone.parent]
    
    # Add new drone to relay and get available port
    port = relay.add_drone(drone.name)

    #print(relay.drones[drone.name].name)
    return { "video_port": port }
    

@relay_router.post("/handshake")
def handle(relay: RelayHandshakeModel, mongo: object = Depends(get_mongo)):
    if not mongo.authenticate(relay):
         raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED)

    # Initialize new Relaybox Class
    relay = Relay(relay.name, active_relays)

    # Store new active relay
    active_relays[relay.name] = relay

    # Generate new HS256 access token
    token = generate_access_token(data={'sub': relay.name}, minutes=24*60)
    return {"access_token": f"Bearer {token}"}