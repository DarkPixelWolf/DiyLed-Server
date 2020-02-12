import json
import os
import socketserver
import threading
import sys
from multiprocessing import Process
import logging
from uuid import getnode as get_mac
import socket
import struct
from http.server import BaseHTTPRequestHandler, HTTPServer
import time
import requests

DEBUG = True

if DEBUG:
    print("*------------------------------------------------*")
    print("Starting server")
    print("")
    print("+ Setting up variables")
config = None
udp = None

MCAST_GRP = '239.255.255.250'
MCAST_PORT = 1900
MULTICAST_TTL = 2

rooms = {}
lights = {}
scenes = {}
appInstances = {}


class AppInstance():
    def __init__(self, ip):
        self.ip = ip
        self.DISCOVER = False
        self.DEAD = False

    def sendMessage(self, message):
        global udp
        udp.sendto(message.encode('utf-8'), (self.ip, 7777))

class Room():
    global config
    global lights
    global scenes

    def __init__(self, name, lights, scenes):
        self.name = name
        self.lights = lights
        self.scenes = scenes
        self.power = False

    def addLight(self, light):
        self.lights.append(light.name)
        if light.power:
            self.power = True
        config.updateRoom(self)

    def removeLight(self, light):
        del self.lights[self.lights.index(light.name)]
        self.power = False
        for lightName in self.lights:
            if lights[lightName].power:
                self.power = True
        config.updateRoom(self)

    def addScene(self, scene):
        self.scenes.append(scene)
        config.updateRoom(self)

    def removeScene(self, scene):
        del self.scenes[self.scenes.index(scene.name)]
        config.updateRoom(self)

    def applyScene(self, scene):
        scenes[scene].applyScene()

    def togglePower(self, newPowerState):
        self.power = newPowerState
        for lightName in self.lights:
            lights[lightName].power = self.power
            jsonData = {
                "id": "changeValueRequestPacket",
                "data": {
                    "request": "light",
                    "name": lightName,
                    "key": "power",
                    "value": str(self.power).lower(),
                    "id": hex(get_mac())
                }
            }
            print(json.dumps(jsonData))
            requests.put("http://" + lights[lightName].ip + ":80/diyledapi/" + str(hex(get_mac())) + "/updateValue",
                         data=json.dumps(jsonData).encode('utf-8'))

    def setRoomBrightness(self, newBrightness):
        for lightName in self.lights:
            lights[lightName].brightness = newBrightness
            jsonData = {
                "id": "changeValueRequestPacket",
                "data": {
                    "request": "light",
                    "name": lightName,
                    "key": "brightness",
                    "value": int(newBrightness),
                    "id": hex(get_mac())
                }
            }
            requests.put("http://" + lights[lightName].ip + ":80/diyledapi/" + str(hex(get_mac())) + "/updateValue",
                         data=json.dumps(jsonData).encode('utf-8'))

    def updatePowerState(self):
        self.power = False
        for light in self.lights:
            if lights[light].power:
                self.power = True

    def getInfoPacket(self):
        data = {
            "id": "roomPacket",
            "data": {
                "name": self.name,
                "lights": self.lights,
                "power": self.power,
                "scenes": self.scenes
            }
        }
        return data

class Light():
    def __init__(self, name, rooms, ledCount, color, mode, power, brightness, modes, ip):
        self.name = name
        self.rooms = rooms
        self.ledCount = ledCount
        self.color = color
        self.brightness = brightness
        self.mode = mode
        self.power = power
        self.modes = modes
        self.ip = ip

    def addRoom(self, room):
        self.rooms.append(room.name)
        config.updateLight(self)

    def removeRoom(self, room):
        del self.rooms[self.rooms.index(room.name)]
        config.updateLight(self)

    def togglePower(self, newPowerState):
        self.power = newPowerState

    def getInfoPacket(self):
        data = {
            "id": "lightPacket",
            "data": {
                "name": self.name,
                "rooms": self.rooms,
                "color": [self.color.r, self.color.g, self.color.b],
                "brightness": self.brightness,
                "mode": self.mode,
                "power": self.power,
                "ledCount": self.ledCount,
                "modes": self.modes
            }
        }
        return data

class Scene():
    global lights

    def __init__(self, name, room, lightStates):
        self.name = name
        self.room = room
        self.lightStates = lightStates

    def addLightState(self, light, color, mode, power, brightness):
        self.lightStates[light.name] = {"color": color, "mode": mode, "power": power, "brightness": brightness}

    def removeLightState(self, light):
        del self.lightStates[light.name]

    def applyScene(self):
        if DEBUG:
            print("SERVER: applying Scene: " + self.name + " of " + self.room + " for " + str(
                len(lightStates)) + " lights")
        for light in self.lightStates:
            l = lights[light]
            stateJson = self.lightStates[light]
            l.color = stateJson["color"]
            l.brightness = int(stateJson["brightness"])
            l.mode = str(stateJson["mode"])
            l.power = json.loads(str(stateJson["power"]).lower())
            jsonData = {
                "id": "applyScenePacket",
                "data": {
                    "color": [l.color.r, l.color.g, l.color.b],
                    "brightness": l.brightness,
                    "mode": l.mode,
                    "power": str(l.power).lower(),
                    "id": hex(get_mac())
                }
            }
            requests.put("http://" + l.ip + ":80/diyledapi/" + str(hex(get_mac())) + "/applyScene",
                         data=json.dumps(jsonData).encode('utf-8'))

    def getInfoPacket(self):
        lightStateJsons = []
        for lightState in self.lightStates:
            ls = self.lightStates[lightState]
            lightStateJson = {
                "name": lightState,
                "color": [ls["color"].r, ls["color"].g, ls["color"].b],
                "mode": ls["mode"],
                "power": ls["power"],
                "brightness": ls["brightness"]
            }
            lightStateJsons.append(lightStateJson)

        data = {
            "id": "scenePacket",
            "data": {
                "name": self.name,
                "room": self.room,
                "lightStates": lightStateJsons
            }
        }
        return data

class LedColor():
    def __init__(self, r, g, b):
        self.r = r
        self.g = g
        self.b = b


class Config():
    global lights
    global rooms

    def __init__(self, path):
        self.path = path
        self.configLoaded = False
        self.load()

    # -- CONFIG functions
    def createDefault(self):
        data = {
            "server": {
                "ip": "localhost",
                "port": 7557,
                "mqtt": "False",
                "mqtthost": "localhost",
                "mqttport": 1883,
                "mqttauth": "False",
                "mqttuser": "",
                "mqttuserpassword": ""
            },
            "rooms": [],
            "lights": [],
            "scenes": []
        }
        with open(self.path, "w") as file:
            json.dump(data, file)

        self.config = data
        self.configLoaded = True

    def load(self):
        if not os.path.isfile(self.path):
            self.createDefault()
        else:
            with open(self.path, "r") as file:
                self.config = json.load(file)
        self.configLoaded = True
        logging.info('CONFIG: Loaded')

    def save(self):
        with open(self.path, "w") as file:
            json.dump(self.config, file)
        logging.info('CONFIG: Saved')

    # -- LIGHT functions
    def getLights(self):
        cLights = {}
        for lightJson in self.config["lights"]:
            cLights[lightJson["name"]] = Light(lightJson["name"], lightJson["rooms"], int(lightJson["ledCount"]),
                                               LedColor(0, 0, 0), 0, False, 0, lightJson["modes"], lightJson["ip"])
        return cLights

    def addLight(self, light):
        lightJson = {
            "name": light.name,
            "rooms": light.rooms,
            "ledCount": light.ledCount,
            "modes": light.modes,
            "ip": light.ip
        }
        self.config["lights"].append(lightJson)
        self.save()

    def removeLight(self, light):
        for i in range(len(self.config["lights"])):
            if self.config["lights"][i]["name"] == light.name:
                for room in light.rooms:
                    rooms[room].removeLight(light.name)
                del self.config["lights"][i]
                break
        del lights[light.name]
        self.save()

    def updateLight(self, light):
        lightJson = {
            "name": light.name,
            "rooms": light.rooms,
            "ledCount": light.ledCount,
            "modes": light.modes,
            "ip": light.ip
        }
        for i in range(len(self.config["lights"])):
            if self.config["lights"][i]["name"] == light.name:
                self.config["lights"][i] = lightJson
                break
        self.save()

    # -- ROOM functions
    def getRooms(self):
        cRooms = {}
        for roomJson in self.config["rooms"]:
            cRooms[roomJson["name"]] = Room(roomJson["name"], roomJson["lights"], roomJson["scenes"])
        return cRooms

    def addRoom(self, room):
        roomJson = {
            "name": room.name,
            "lights": room.lights,
            "scenes": room.scenes
        }
        self.config["rooms"].append(roomJson)
        self.save()

    def removeRoom(self, room):
        for i in range(len(self.config["rooms"])):
            if self.config["rooms"][i]["name"] == room.name:
                del self.config["rooms"][i]
                break
        del rooms[room.name]
        self.save()

    def updateRoom(self, room):
        roomJson = {
            "name": room.name,
            "lights": room.lights,
            "scenes": room.scenes
        }
        for i in range(len(self.config["rooms"])):
            if self.config["rooms"][i]["name"] == room.name:
                self.config["rooms"][i] = roomJson
                break
        self.save()

    # -- SCENE functions
    def getScenes(self):
        cScenes = {}
        for sceneJson in self.config["scenes"]:
            ls = {}
            for lsJson in sceneJson["lightStates"]:
                ls[lsJson["name"]] = {
                    "color": LedColor(int(lsJson["color"][0]), int(lsJson["color"][1]), int(lsJson["color"][2])),
                    "mode": str(lsJson["mode"]), "power": json.loads(str(lsJson["power"]).lower()),
                    "brightness": int(lsJson["brightness"])}
            cScenes[sceneJson["name"]] = Scene(sceneJson["name"], sceneJson["room"], ls)
        return cScenes

    def addScene(self, scene):
        lightStateJsons = []
        for lightState in scene.lightStates:
            ls = scene.lightStates[lightState]
            lightStateJson = {
                "name": lightState,
                "color": [ls["color"].r, ls["color"].g, ls["color"].b],
                "mode": ls["mode"],
                "power": ls["power"],
                "brightness": ls["brightness"]
            }
            lightStateJsons.append(lightStateJson)
        sceneJson = {
            "name": scene.name,
            "room": scene.room,
            "lightStates": lightStateJsons
        }
        if DEBUG:
            print("CONFIG: adding Scene: (" + str(scene.name) + ") " + scene.room + " - " + str(
                len(scene.lightStates)) + " lights")
        self.config["scenes"].append(sceneJson)
        self.save()

    def removeScene(self, scene):
        for i in range(len(self.config["scenes"])):
            if self.config["scenes"][i]["name"] == scene.name:
                del self.config["scenes"][i]
                break
        del scenes[scene.name]
        self.save()

    def updateScene(self, scene):
        lightStateJsons = []
        for lightState in scene.lightStates:
            ls = scene.lightStates[lightState]
            lightStateJson = {
                "name": lightState,
                "color": [ls["color"][0], ls["color"][1], ls["color"][2]],
                "mode": ls["mode"],
                "power": ls["power"],
                "brightness": ls["brightness"]
            }
            lightStateJsons.append(lightStateJson)
        sceneJson = {
            "name": scene.name,
            "room": scene.room,
            "lightStates": lightStateJsons
        }
        for i in range(len(self.config["scenes"])):
            if self.config["scenes"][i]["name"] == scene.name:
                self.config["scenes"][i] = sceneJson
                break
        self.save()

def handleRequest(jsonData, handler, ISUDP=False):
    packetType = jsonData["id"]
    if packetType == "infoRequestPacket":
        if jsonData["data"]["request"] == "room":
            jsonReturn = rooms[jsonData["data"]["name"]].getInfoPacket()
        elif jsonData["data"]["request"] == "light":
            jsonReturn = lights[jsonData["data"]["name"]].getInfoPacket()
        elif jsonData["data"]["request"] == "allRooms":
            roomInfoPackets = []
            for name in rooms:
                roomInfoPackets.append(rooms[name].getInfoPacket())
            jsonReturn = {
                "id": "allRoomsPacket",
                "data": {
                    "rooms": roomInfoPackets,
                    "id": jsonData["data"]["id"]
                }
            }
            if not ISUDP:
                handler.send_response(200)
                handler.send_header('Content-type', 'application/json')
                handler.end_headers()
                handler.wfile.write(json.dumps(jsonReturn).encode('utf-8'))
        elif jsonData["data"]["request"] == "allLights":
            lightInfoPackets = []
            for name in lights:
                lightInfoPackets.append(lights[name].getInfoPacket())
            jsonReturn = {
                "id": "allLightsPacket",
                "data": {
                    "lights": lightInfoPackets,
                    "id": jsonData["data"]["id"]
                }
            }
            if not ISUDP:
                handler.send_response(200)
                handler.send_header('Content-type', 'application/json')
                handler.end_headers()
                handler.wfile.write(json.dumps(jsonReturn).encode('utf-8'))
        elif jsonData["data"]["request"] == "lightsOfRoom":
            lightInfoPackets = []
            for name in rooms[jsonData["data"]["name"]].lights:
                lightInfoPackets.append(lights[name].getInfoPacket())
            jsonReturn = {
                "id": "lightsOfRoomPacket",
                "data": {
                    "name": jsonData["data"]["name"],
                    "lights": lightInfoPackets,
                    "id": jsonData["data"]["id"]
                }
            }
            if not ISUDP:
                handler.send_response(200)
                handler.send_header('Content-type', 'application/json')
                handler.end_headers()
                handler.wfile.write(json.dumps(jsonReturn).encode('utf-8'))
        elif jsonData["data"]["request"] == "scene":
            jsonReturn = scenes[jsonData["data"]["name"]].getInfoPacket()
        elif jsonData["data"]["request"] == "allScenes":
            sceneInfoPackets = []
            for name in scenes:
                sceneInfoPackets.append(scenes[name].getInfoPacket())
            jsonReturn = {
                "id": "allScenesPacket",
                "data": {
                    "scenes": sceneInfoPackets,
                    "id": jsonData["data"]["id"]
                }
            }
            if not ISUDP:
                handler.send_response(200)
                handler.send_header('Content-type', 'application/json')
                handler.end_headers()
                handler.wfile.write(json.dumps(jsonReturn).encode('utf-8'))
        elif jsonData["data"]["request"] == "scenesOfRoom":
            sceneInfoPackets = []
            for name in rooms[jsonData["data"]["name"]].scenes:
                sceneInfoPackets.append(scenes[name].getInfoPacket())
            jsonReturn = {
                "id": "cenesOfRoomPacket",
                "data": {
                    "name": jsonData["data"]["name"],
                    "scenes": sceneInfoPackets,
                    "id": jsonData["data"]["id"]
                }
            }
            if not ISUDP:
                handler.send_response(200)
                handler.send_header('Content-type', 'application/json')
                handler.end_headers()
                handler.wfile.write(json.dumps(jsonReturn).encode('utf-8'))
    elif packetType == "createRequestPacket":
        if jsonData["data"]["request"] == "room":
            jsonReturn = ""
            if not jsonData["data"]["name"] in rooms:
                nRoom = Room(jsonData["data"]["name"], [], [])
                rooms[jsonData["data"]["name"]] = nRoom
                config.addRoom(nRoom)
                if config.config["server"]["mqttauth"] == "True":
                    client.subscribe(nRoom.name)
                jsonReturn = {
                    "id": "successPacket",
                    "data": {
                        "message": "Raum erstellt.",
                        "id": jsonData["data"]["id"]
                    }
                }
            else:  # if the room exists throw error, shouldn't happen though (exception handling should be handled within the requesting client)
                jsonReturn = {
                    "id": "errorPacket",
                    "data": {
                        "message": "Ein Raum mit diesem Namen existiert bereits.",
                        "id": jsonData["data"]["id"]
                    }
                }
            if not ISUDP:
                handler.send_response(200)
                handler.send_header('Content-type', 'application/json')
                handler.end_headers()
                handler.wfile.write(json.dumps(jsonReturn).encode('utf-8'))
        elif jsonData["data"][
            "request"] == "light":  # used as a register and setup function! lights should send an initial packet with the createPacketRequest.light id
            if not jsonData["data"]["name"] in lights:  # register if unknown
                nLight = Light(jsonData["data"]["name"], [], int(jsonData["data"]["ledCount"]),
                               LedColor(int(jsonData["data"]["color"][0]), int(jsonData["data"]["color"][1]),
                                        int(jsonData["data"]["color"][2])), str(jsonData["data"]["mode"]),
                               bool(jsonData["data"]["power"]), int(jsonData["data"]["brightness"]),
                               jsonData["data"]["modes"], jsonData["data"]["ip"])
                lights[jsonData["data"]["name"]] = nLight
                config.addLight(nLight)
                if config.config["server"]["mqttauth"] == "True":
                    client.subscribe(nLight.name)
            else:  # light already exists, set initial/last known values
                l = lights[jsonData["data"]["name"]]
                l.color = LedColor(int(jsonData["data"]["color"][0]), int(jsonData["data"]["color"][1]),
                                   int(jsonData["data"]["color"][2]))
                l.mode = str(jsonData["data"]["mode"])
                l.brightness = int(jsonData["data"]["brightness"])
                l.power = bool(jsonData["data"]["power"])
                l.modes = jsonData["data"]["modes"]
                l.ip = jsonData["data"]["ip"]
                for room in l.rooms:
                    rooms[room].updatePowerState()
            jsonReturn = {
                "id": "successPacket",
                "data": {
                    "message": "Licht registriert.",  # always return success, no error needed
                    "id": jsonData["data"]["id"]
                }
            }
            if not ISUDP:
                handler.send_response(200)
                handler.send_header('Content-type', 'application/json')
                handler.end_headers()
                handler.wfile.write(json.dumps(jsonReturn).encode('utf-8'))

        elif jsonData["data"]["request"] == "scene":
            if not jsonData["data"]["name"] in scenes:
                ls = {}
                for lsJson in jsonData["data"]["lightStates"]:
                    ls[lsJson["name"]] = {
                        "color": LedColor(int(lsJson["color"][0]), int(lsJson["color"][1]), int(lsJson["color"][2])),
                        "mode": str(lsJson["mode"]), "power": json.loads(str(lsJson["power"]).lower()),
                        "brightness": int(lsJson["brightness"])}
                nScene = Scene(jsonData["data"]["name"], jsonData["data"]["room"], ls)
                scenes[nScene.name] = nScene
                rooms[nScene.room].addScene(nScene.name)
                config.addScene(nScene)
            jsonReturn = {
                "id": "successPacket",
                "data": {
                    "message": "Scene erstellt.",  # always return success, no error needed
                    "id": jsonData["data"]["id"]
                }
            }
            if not ISUDP:
                handler.send_response(200)
                handler.send_header('Content-type', 'application/json')
                handler.end_headers()
                handler.wfile.write(json.dumps(jsonReturn).encode('utf-8'))
    elif packetType == "editRequestPacket":
        if jsonData["data"]["request"] == "lightsOfRoom":
            room = rooms[jsonData["data"]["name"]]
            removeList = [x for x in room.lights if x not in jsonData["data"]["lights"]]
            for name in removeList:
                room.removeLight(lights[name])
                lights[name].removeRoom(room)
            for name in jsonData["data"]["lights"]:
                if not name in room.lights:
                    room.addLight(lights[name])
                    lights[name].addRoom(room)
            room.updatePowerState()
            jsonReturn = {
                "id": "successPacket",
                "data": {
                    "message": "Lichter des Raums bearbeitet.",  # always return success, no error needed
                    "id": jsonData["data"]["id"]
                }
            }
            if not ISUDP:
                handler.send_response(200)
                handler.send_header('Content-type', 'application/json')
                handler.end_headers()
                handler.wfile.write(json.dumps(jsonReturn).encode('utf-8'))
        if jsonData["data"]["request"] == "lightStatesOfScene":
            scene = scenes[jsonData["data"]["name"]]
            ls = {}
            for lsJson in jsonData["data"]["lightStates"]:
                ls[lsJson["name"], {
                    "color": LedColor(int(lsJson["color"][0]), int(lsJson["color"][1]), int(lsJson["color"][2])),
                    "mode": int(lsJson["mode"]), "power": json.loads(str(lsJson["power"]).lower()),
                    "brightness": int(lsJson["brightness"])}]
            scene.lightStates = ls
            config.updateScene(scene)
            jsonReturn = {
                "id": "successPacket",
                "data": {
                    "message": "Lichtstand der Scene bearbeitet.",  # always return success, no error needed
                    "id": jsonData["data"]["id"]
                }
            }
            if not ISUDP:
                handler.send_response(200)
                handler.send_header('Content-type', 'application/json')
                handler.end_headers()
                handler.wfile.write(json.dumps(jsonReturn).encode('utf-8'))
    elif packetType == "removeRequestPacket":
        if jsonData["data"]["request"] == "room":
            room = rooms[jsonData["data"]["name"]]
            for lightName in room.lights:
                lights[lightName].removeRoom(room)
            config.removeRoom(room)
            jsonReturn = {
                "id": "successPacket",
                "data": {
                    "message": "Raum gelöscht.",
                    "id": jsonData["data"]["id"]
                }
            }
            if not ISUDP:
                handler.send_response(200)
                handler.send_header('Content-type', 'application/json')
                handler.end_headers()
                handler.wfile.write(json.dumps(jsonReturn).encode('utf-8'))
        if jsonData["data"]["request"] == "light":
            light = lights[jsonData["data"]["name"]]
            for roomName in light.rooms:
                rooms[roomName].removeLight(light)
            config.removeLight(light)
            jsonReturn = {
                "id": "successPacket",
                "data": {
                    "message": "Licht gelöscht.",
                    "id": jsonData["data"]["id"]
                }
            }
            if not ISUDP:
                handler.send_response(200)
                handler.send_header('Content-type', 'application/json')
                handler.end_headers()
                handler.wfile.write(json.dumps(jsonReturn).encode('utf-8'))
        if jsonData["data"]["request"] == "scene":
            scene = scenes[jsonData["data"]["name"]]
            rooms[scene.room].removeScene(scene)
            config.removeScene(scene)
            jsonReturn = {
                "id": "successPacket",
                "data": {
                    "message": "scene gelöscht.",
                    "id": jsonData["data"]["id"]
                }
            }
            if not ISUDP:
                handler.send_response(200)
                handler.send_header('Content-type', 'application/json')
                handler.end_headers()
                handler.wfile.write(json.dumps(jsonReturn).encode('utf-8'))
    elif packetType == "changeValueRequestPacket":
        if jsonData["data"]["request"] == "room":
            if jsonData["data"]["key"] == "power":
                if jsonData["data"]["value"] == "toggle":
                    rooms[jsonData["data"]["name"]].togglePower(not rooms[jsonData["data"]["name"]].power)
                else:
                    rooms[jsonData["data"]["name"]].togglePower(json.loads(jsonData["data"]["value"].lower()))
                jsonReturn = {
                    "id": "successPacket",
                    "data": {
                        "message": "Raumzustand geaendert.",
                        "id": jsonData["data"]["id"]
                    }
                }
                if not ISUDP:
                    handler.send_response(200)
                    handler.send_header('Content-type', 'application/json')
                    handler.end_headers()
                    handler.wfile.write(json.dumps(jsonReturn).encode('utf-8'))
            elif jsonData["data"]["key"] == "brightness":
                rooms[jsonData["data"]["name"]].setRoomBrightness(int(jsonData["data"]["value"]))
                jsonReturn = {
                    "id": "successPacket",
                    "data": {
                        "message": "Raumhelligkeit geaendert.",
                        "id": jsonData["data"]["id"]
                    }
                }
                if not ISUDP:
                    handler.send_response(200)
                    handler.send_header('Content-type', 'application/json')
                    handler.end_headers()
                    handler.wfile.write(json.dumps(jsonReturn).encode('utf-8'))
        elif jsonData["data"]["request"] == "light":
            if jsonData["data"]["key"] == "power":
                if jsonData["data"]["value"] == "toggle":
                    lights[jsonData["data"]["name"]].togglePower(not lights[jsonData["data"]["name"]].power)
                else:
                    lights[jsonData["data"]["name"]].togglePower(json.loads(jsonData["data"]["value"].lower()))
                if config.config["server"]["mqttauth"] == "True":
                    client.publish(jsonData["data"]["name"],
                                   payload=str(lights[jsonData["data"]["name"]].power).lower(), qos=0, retain=False)
                for room in lights[jsonData["data"]["name"]].rooms:
                    rooms[room].updatePowerState()
                response = requests.put("http://" + lights[jsonData["data"]["name"]].ip + ":80/diyledapi/" + str(
                    hex(get_mac())) + "/updateValue", data=json.dumps(jsonData).encode('utf-8'))
                response = json.loads(response.content.decode('utf-8'))
                jsonReturn = ""
                if response["id"] == "successPacket":
                    jsonReturn = {
                        "id": "successPacket",
                        "data": {
                            "message": "Lichtzustand geaendert.",
                            "id": jsonData["data"]["id"]
                        }
                    }
                else:
                    jsonReturn = {
                        "id": "errorPacket",
                        "data": {
                            "message": "Lichtzustand konnte nicht geaendert werden.",
                            "id": jsonData["data"]["id"]
                        }
                    }
                if not ISUDP:
                    handler.send_response(200)
                    handler.send_header('Content-type', 'application/json')
                    handler.end_headers()
                    handler.wfile.write(json.dumps(jsonReturn).encode('utf-8'))
            if jsonData["data"]["key"] == "brightness":
                lights[jsonData["data"]["name"]].brightness = int(jsonData["data"]["value"])
                response = requests.put("http://" + lights[jsonData["data"]["name"]].ip + ":80/diyledapi/" + str(
                    hex(get_mac())) + "/updateValue", data=json.dumps(jsonData).encode('utf-8'))
                response = json.loads(response.content.decode('utf-8'))
                jsonReturn = ""
                if response["id"] == "successPacket":
                    jsonReturn = {
                        "id": "successPacket",
                        "data": {
                            "message": "Lichthelligkeit geaendert.",
                            "id": jsonData["data"]["id"]
                        }
                    }
                else:
                    jsonReturn = {
                        "id": "errorPacket",
                        "data": {
                            "message": "Lichthelligkeit konnte nicht geaendert werden.",
                            "id": jsonData["data"]["id"]
                        }
                    }
                if not ISUDP:
                    handler.send_response(200)
                    handler.send_header('Content-type', 'application/json')
                    handler.end_headers()
                    handler.wfile.write(json.dumps(jsonReturn).encode('utf-8'))
            if jsonData["data"]["key"] == "mode":
                lights[jsonData["data"]["name"]].mode = str(jsonData["data"]["value"])
                response = requests.put("http://" + lights[jsonData["data"]["name"]].ip + ":80/diyledapi/" + str(
                    hex(get_mac())) + "/updateValue", data=json.dumps(jsonData).encode('utf-8'))
                response = json.loads(response.content.decode('utf-8'))
                jsonReturn = ""
                if response["id"] == "successPacket":
                    jsonReturn = {
                        "id": "successPacket",
                        "data": {
                            "message": "Lichtmodus geaendert.",
                            "id": jsonData["data"]["id"]
                        }
                    }
                else:
                    jsonReturn = {
                        "id": "errorPacket",
                        "data": {
                            "message": "Lichtmodus konnte nicht geaendert werden.",
                            "id": jsonData["data"]["id"]
                        }
                    }
                if not ISUDP:
                    handler.send_response(200)
                    handler.send_header('Content-type', 'application/json')
                    handler.end_headers()
                    handler.wfile.write(json.dumps(jsonReturn).encode('utf-8'))
            if jsonData["data"]["key"] == "color":
                lights[jsonData["data"]["name"]].color = LedColor(int(jsonData["data"]["value"][0]),
                                                                  int(jsonData["data"]["value"][1]),
                                                                  int(jsonData["data"]["value"][2]))
                response = requests.put("http://" + lights[jsonData["data"]["name"]].ip + ":80/diyledapi/" + str(
                    hex(get_mac())) + "/updateValue", data=json.dumps(jsonData).encode('utf-8'))
                response = json.loads(response.content.decode('utf-8'))
                jsonReturn = ""
                if response["id"] == "successPacket":
                    jsonReturn = {
                        "id": "successPacket",
                        "data": {
                            "message": "Lichtfarbe geaendert.",
                            "id": jsonData["data"]["id"]
                        }
                    }
                else:
                    jsonReturn = {
                        "id": "errorPacket",
                        "data": {
                            "message": "Lichtfarbe konnte nicht geaendert werden.",
                            "id": jsonData["data"]["id"]
                        }
                    }
                if not ISUDP:
                    handler.send_response(200)
                    handler.send_header('Content-type', 'application/json')
                    handler.end_headers()
                    handler.wfile.write(json.dumps(jsonReturn).encode('utf-8'))
        if jsonData["data"]["request"] == "scene":
            if jsonData["data"]["key"] == "apply":
                scenes[jsonData["data"]["name"]].applyScene()
                jsonReturn = {
                    "id": "successPacket",
                    "data": {
                        "message": "Scene wird angewendet.",
                        "id": jsonData["data"]["id"]
                    }
                }
                if not ISUDP:
                    handler.send_response(200)
                    handler.send_header('Content-type', 'application/json')
                    handler.end_headers()
                    handler.wfile.write(json.dumps(jsonReturn).encode('utf-8'))
    return jsonReturn

class httpHandler(BaseHTTPRequestHandler):
    global appInstances

    def do_GET(self):
        path = str(self.path)
        if DEBUG:
            print("HTTP: handling request: '" + path + "' from " + str(self.client_address))
        elif (path.startswith("/diyledstatus")):
            active = 0
            dead = 0
            for app in appInstances:
                if appInstances[app].DEAD:
                    dead = dead + 1
                else:
                    active = active + 1
            response = "DiyLed - Status:\r\n\r\nRunning... \r\n%s Lights registered!\r\n%s Rooms registered!\r\n%s Scenes registered!\r\nAppInstances: %s Active, %s Dead\r\n\r\nStates:\r\n" % (
            str(len(lights)), str(len(rooms)), str(len(scenes)), str(active), str(dead))
            for lightName in lights:
                response = response + str(lightName) + " - Power: " + str(
                    lights[lightName].power).lower() + " | Brightness: " + str(
                    lights[lightName].brightness) + " | Mode: " + str(lights[lightName].mode) + " | Color: " + str(
                    lights[lightName].color.r) + ", " + str(lights[lightName].color.g) + ", " + str(
                    lights[lightName].color.b) + "\r\n"
            response = response + "\r\n\r\nDiyLed V1.1 by Sebastian Scheibe, 2019"
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(response.encode('utf-8'))
            return
        elif (path.startswith("/diyled")):
            content_len = int(self.headers.get('Content-Length', 0))
            post_body = self.rfile.read(content_len).decode('utf-8')
            try:
                if (json.loads(post_body)):
                    handleRequest(json.loads(post_body), self)
            except Exception as e:
                if DEBUG:
                    print("HTTP: error handling '/diyled' request from " + str(self.client_address))
                    print(e)
            return
        if DEBUG:
            print("HTTP: error handling request from " + str(self.client_address))
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(("ERROR").encode('utf-8'))
        return

    def do_PUT(self):
        path = str(self.path)
        if DEBUG:
            print("HTTP: handling request: '" + path + "' from " + str(self.client_address))
        if (path.startswith("/diyledinfo")):
            content_len = int(self.headers.get('Content-Length', 0))
            post_body = self.rfile.read(content_len).decode('utf-8')
            if (json.loads(post_body)):
                print(self.client_address)
                handleRequest(json.loads(post_body), self)
            return
        elif (path.startswith("/diyleddiscover")):
            content_len = int(self.headers.get('Content-Length', 0))
            post_body = self.rfile.read(content_len).decode('utf-8')
            if (json.loads(post_body)):
                jsonData = json.loads(post_body)
                ip, port = self.client_address
                print(appInstances.keys())
                appInstances[str(ip)].DISCOVER = True
                searching = True
                t = threading.Thread(target=searchTimer)
                t.daemon = True
                t.start()
                jsonReturn = {
                    "id": "successPacket",
                    "data": {
                        "message": "Suche gestartet.",
                        "id": jsonData["id"]
                    }
                }
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(jsonReturn).encode('utf-8'))
        elif (path.startswith("/diyledapp")):
            content_len = int(self.headers.get('Content-Length', 0))
            post_body = self.rfile.read(content_len).decode('utf-8')
            if (json.loads(post_body)):
                ip, port = self.client_address
                appInstances[ip] = AppInstance(ip)
                jsonData = json.loads(post_body)
                lightInfoPackets = []
                for name in lights:
                    lightInfoPackets.append(lights[name].getInfoPacket())
                roomInfoPackets = []
                for name in rooms:
                    roomInfoPackets.append(rooms[name].getInfoPacket())
                sceneInfoPackets = []
                for name in scenes:
                    sceneInfoPackets.append(scenes[name].getInfoPacket())
                jsonReturn = {
                    "id": "setupPackets",
                    "data": [
                        {
                            "id": "allLightsPacket",
                            "data": {
                                "lights": lightInfoPackets,
                                "id": jsonData["id"]
                            }
                        },
                        {
                            "id": "allRoomsPacket",
                            "data": {
                                "rooms": roomInfoPackets,
                                "id": jsonData["id"]
                            }
                        },
                        {
                            "id": "allScenesPacket",
                            "data": {
                                "scenes": sceneInfoPackets,
                                "id": jsonData["id"]
                            }
                        }
                    ]
                }
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(jsonReturn).encode('utf-8'))
                return
        elif (path.startswith("/diyled")):
            content_len = int(self.headers.get('Content-Length', 0))
            post_body = self.rfile.read(content_len).decode('utf-8')
            if (json.loads(post_body)):
                print(self.client_address)
                handleRequest(json.loads(post_body), self)
                ip, port = self.client_address
                for app in appInstances:
                    if appInstances[app].ip != ip:
                        jsonData = {
                            "id": "getSetupPackets"
                        }
                        appInstances[app].sendMessage(json.dumps(jsonData))
            return
        if DEBUG:
            print("HTTP: error handling request from " + str(self.client_address))
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(("ERROR").encode('utf-8'))
        return

class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    pass

def startHTMLServer():
    server = ThreadedHTTPServer(('', 80), httpHandler)
    tServer = threading.Thread(target=server.serve_forever)
    tServer.daemon = True
    tServer.start()
    if DEBUG:
        print("  -> TCP Server started")

def startUDPServer():
    global udp
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    udp.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 32)
    udp.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    udp.bind(('', MCAST_PORT))
    host = socket.gethostbyname(socket.gethostname())
    udp.setsockopt(socket.SOL_IP, socket.IP_MULTICAST_IF, socket.inet_aton(host))
    mreq = struct.pack("4sl", socket.inet_aton(MCAST_GRP), socket.INADDR_ANY)
    udp.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    if DEBUG:
        print("  -> UDP Server started")

def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't even have to be reachable
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

def handleUDP():
    global udp
    global lights
    global config
    global newLights
    global searching
    while True:
        request, request_addr = udp.recvfrom(1024)
        request = request.decode('utf-8')
        if (request):
            if DEBUG:
                print("UDP: data from: " + str(request_addr))
            if (request.find("HTTP/1.1 200 OK") >= 0) and (request.find("urn:diyleddevice:light") >= 0):
                s = request.split("\r\n")
                address = s[3].split("LOCATION: ")[1]
                try:
                    response = requests.get(address)
                    conf = response.json()
                    if DEBUG:
                        print("UDP: responding 'HTTP/1.1 200 OK' of " + str(request_addr))
                    handleRequest(conf, None, ISUDP=True)
                    if searching:
                        newLights.append(conf["data"]["name"])
                except Exception as e:
                    if DEBUG:
                        print(e)
            elif (request.find("M-SEARCH * HTTP/1.1") >= 0) and (request.find("urn:diyleddevice:server") >= 0):
                print(request)
                localIP = get_ip()
                response = "\r\n".join([
                    'HTTP/1.1 200 OK',
                    'EXT:',
                    'CACHE-CONTROL: max-age=100',
                    'LOCATION: http://' + localIP + ':80/diyledapp',
                    'SERVER: DiyLed/1.1, UPnP/1.0, DiyLedServer/1.1',
                    'ST: urn:diyleddevice:server',
                    'USN: uuid:' + str(hex(get_mac())) + '::urn:diyleddevice', '', ''])
                ip, port = request_addr
                if DEBUG:
                    print("UDP: responding 'M-SEARCH * HTTP/1.1' of " + str(request_addr))
                udp.sendto(response.encode('utf-8'), (ip, port))

def searchForDevices():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 32)
    message = "\r\n".join([
        'M-SEARCH * HTTP/1.1',
        'HOST: ' + str(MCAST_GRP) + ':' + str(MCAST_PORT),
        'MAN: "ssdp:discover"',
        'ST: urn:diyleddevice:light',
        'MX: 2',
        'USER-AGENT: DiyLed/1.1 DiyLedServer/1.1', '', ''])
    sock.sendto(message.encode('utf-8'), (MCAST_GRP, MCAST_PORT))

def searchTimer():
    timer = 0
    while (timer < 30):
        time.sleep(1)
        timer = timer + 1
    searching = False

    jsonData = {
        "id": "discoverResultPacket",
        "data": {
            "lights": newLights,
            "id": hex(get_mac())
        }
    }
    print(str(jsonData))
    for app in appInstances:
        if (appInstances[app].DISCOVER):
            appInstances[app].sendMessage(json.dumps(jsonData))
            appInstances[app].DISCOVER = False

config = Config("config.json")
lights = config.getLights()
rooms = config.getRooms()
scenes = config.getScenes()

newLights = []
searching = False

if DEBUG:
    print("  -> " + str(len(lights)) + " lights")
    print("  -> " + str(len(rooms)) + " rooms")
    print("  -> " + str(len(scenes)) + " scenes")
    print("- Variable setup complete")

if __name__ == "__main__":
    if DEBUG:
        print("+ Starting subservers")
    startUDPServer()
    startHTMLServer()
    t = threading.Thread(target=handleUDP)
    t.daemon = True
    t.start()
    if DEBUG:
        print("- Starting subservers")
        print("Searching for devices")
    searchForDevices()
    searchForDevices()

    while True:
        try:
            time.sleep(20)
        except KeyboardInterrupt:
            print("Exit")
            sys.exit(0)