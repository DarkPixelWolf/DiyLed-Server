## DiyLed-Server
This is the serverside of the DiyLed system.

* [Client for python]()
* [Client for esp]()

#### What is the DiyLed system?
The DiyLed system is my version of systems/enviroments like Philips Hue (sort of).
This system was created to 
* allow the use of any device that is not supported by Amazon Alexa and Philips Hue
* give the user a overall higher customizability regarding control, light effects etc
* be a kind of challenge/project for myself to be honest

#### Functions
* fully customizable server and client (for python3 and esp)
* full Alexa integration
* compatible DiyLed App
* no static ip setup needed (is recommended though)

#### Installation
*I highly recommend using a Raspberry Pi of any revision (with LAN is better) as it supports python3, doesn't use a lot of power and was used to test the server.*

The DiyLed Server requieres
* [Espalexa-Python library](https://github.com/DarkPixelWolf/Espalexa-Python) (based on [Espalexa](https://github.com/Aircoookie/Espalexa) by Aircoookie).
* [socketserver](https://github.com/python/cpython/blob/2.7/Lib/SocketServer.py) python library which might be included in your python installation, but was missing in mine

Otherwise the installation is very simple, just place the DiyLedServer.py file in any directory you like (this should include the [espalexa.py](https://github.com/DarkPixelWolf/Espalexa-Python)).
That is it! Now you can start the server with
```
python3 DiyLedServer.py
```
The server will automatically create a configuration file called `config.json` and is reachable under `http://<server_ip>:80/diyledstatus`.

If you want the server to start at startup you have to create a startup script yourself, if you are using a Raspberry Pi you might use this as a template:

Create a new service file
```
sudo nano /etc/systemd/system/diyledserver.service
```
and paste the following
```
[Unit]
Description=DiyLedServer by Sebastian Scheibe
After=network.target

[Service]
ExecStart=/usr/bin/python3 -u DiyLedServer.py
WorkingDirectory=/home/pi/
Restart=always
User=root

[Install]
WantedBy=multi-user.target
```
You might have to change the python path, working directory or filename according to your needs.

#### How does this work?
The DiyLed server manages lights/devices, Alexa calls and App inputs by exchanging json strings via HTTP requests.

Every DiyLed device sends a udp multicast packet upon startup which is received by the server which then asks for the lights state (e.g. brightness, color, mode etc) and saves the light name, ip and led count into the `config.json`.

If an App or Alexa command wants to change a light, the server sends a http request to the corresponding light and tries to change its state. The light responds with an success or error packet (json string) which decides if the action was an success or not.
