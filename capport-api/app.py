from flask import Flask, request, json, redirect, render_template

import os
import time

import model.database
import model.session
import model.requirement

from subprocess import call, check_output, CalledProcessError

app = Flask(__name__)

####################
## helper methods ##
####################

## get_usage will query the current data usage for this subscriber
def get_usage(identity):
    ## TODO: some implementation to get current data usage
    return 0

## enable_traffic will disable the captive portal
def enable_traffic(identity):
    ## TODO: all requirements are met, signal pcef?
    return

## enable_traffic used by the api
def enable_traffic(identity, id_type):
    print identity, id_type
    activate = "sudo chilli_query authorize " + id_type + " " + identity + " maxbwup 1000 " + "maxbwdown 1000 "
    activated = call(activate, shell=True)
    return

## session_status will return the current state of the session as a json
## object.
def session_status(session, retcode=200):
    usage = get_usage(session.getIdentity())
    struct = {
        "id": { "uuid": session.getId(),
                "href": request.url_root+"capport/sessions/"+session.getId() },
        "identity": session.getIdentity(),
        "cred_api_url": request.url_root+"capport/login/"+session.getId(),
        "state": { "permitted": bool(session.isPermitted(usage)) }
    }

    if (session.isPermitted(usage) is True):
        struct['state']['expires'] = time.strftime("%Y-%m-%dT%H:%M:%S+00:00",time.gmtime(session.getExpire()))
        struct['token'] = session.getToken()
        if session.getDataLimit()>0:
            struct['state']['bytes_remaining'] = session.getDataLimit()-usage

    reqs = session.getRequirements()
    struct['requirements'] = []
    for i in range(len(reqs)):
        struct['requirements'].append({ reqs[i].getType(): reqs[i].getUrl() })
    return (json.dumps(struct), retcode)

## request_wants_json will check the accept headers to determine wether a
## json response is more appropriate.
def request_wants_json():
    best = request.accept_mimetypes \
        .best_match(['application/json', 'text/html'])
    return best == 'application/json' and \
        request.accept_mimetypes[best] > \
        request.accept_mimetypes['text/html']

## Check if the identity (the mac or IP or something else) is in the chilli sessions
def check_identity(identity, id_type):
    try:
        chilli_sessions = check_output("sudo chilli_query /usr/local/var/run/chilli.br-capport.sock list", shell=True)
        if identity in chilli_sessions.split() and id_type in ["username", "mac", "ip"]:
            #activate = "sudo chilli_query authorize " + id_type + " " + identity + "maxbwup 1000 " + "maxbwdown 1000 "
            #print "trying to activate with: " + activate
            #activated = call(activate, shell=True)
            #print "result of activation call: "
            return True
        else:
            return False
    except CalledProcessError:
        print "exception called"
        return False


## Logoff the station in the chilli portal
def logoff_identity(identity, id_type):
    try:
        deactivate = "sudo chilli_query logoff " + id_type + " " + identity
        deactivated = call(deactivate, shell=True)
        return True
    except CalledProcessError:
        print "could not deactivate"
        return False



####################
## captive portal ##
####################

@app.route('/')
def index():
    ## TODO: this is the entry-point to an 'old-fashioned' captive portal, e.g.
    ## when the api is called without the 'application/json' accept header.
    ## I am not sure if this should be an identical implementation as the api
    ## implementation, maybe this could be implemented as it is done in current
    ## captive portals).
    return app.send_static_file('index.html')

# terms page; check for if terms were accepted and delete requirement.
@app.route('/terms')
def terms():
    session_uuid = request.args.get('session')
    if (session_uuid is None):
        return app.send_static_file('invalid.html')

    ## load the session with given session id
    session = model.session.loadSession(session_uuid)
    if (session is None):
        return app.send_static_file('invalid.html')

    ## check if terms
    accept = request.args.get('accept')
    if (accept is None):
        return render_template('terms.html', session = session_uuid)

    ## IDEA: this is probably not the best way to address requirements,
    ## it might be better if they have a unique id instead. The semantic
    ## doesn't add value in this implementation...
    req = model.requirement.loadRequirement(session_uuid,"view_page")
    if (req is not None):
        req.delete()

    ## all requirements are met, disable captive portal
    if (session.metRequirements() is True):
        enable_traffic(session.getIdentity())

    return app.send_static_file('accepted.html')

# login page; check for if password was given and delete requirement.
@app.route('/login')
def login():
    session_uuid = request.args.get('session')
    if (session_uuid is None):
        return app.send_static_file('invalid.html')

    ## load the session with given session id
    session = model.session.loadSession(session_uuid)
    if (session is None):
        return app.send_static_file('invalid.html')

    ## check for password
    passwd = request.args.get('password')
    if (passwd is None):
        return render_template('login.html', session = session_uuid)

    ## IDEA: this is probably not the best way to address requirements,
    ## it might be better if they have a unique id instead. The semantic
    ## doesn't add value in this implementation...
    req = model.requirement.loadRequirement(session_uuid,"provide_credentials")
    if (req is not None):
        req.delete()

    ## all requirements are met, disable captive portal
    if (session.metRequirements() is True):
        enable_traffic(session.getIdentity())

    return app.send_static_file('welcome.html')

##############
## REST API ##
##############

# GET from the DHCP-provided URL:
# GET http://<server>/capport (Accept: application/json)
# 200 OK
@app.route('/capport', methods = ['GET'] )
def capport():
    ## get the create and browse urls from env, default to something fancy...
    create = os.getenv( "CAPPORT_CREATE_SESSION_URL",
                        request.url_root+"capport/sessions")
    browse = os.getenv( "CAPPORT_BROWSE_URL",
                        request.url_root )

    ## in case of json, return the urls
    if request_wants_json():
        return (json.dumps({ "create_href": create,
                             "browse_href": browse }),200)

    ## if request doesn't accept json; redirect to browse url
    return redirect(browse, code=307)

# Posting to the create_href:
# POST http://<server>/capport/sessions (Accept: application/json)
# { "identity": "USERNAME|IP|MAC", "id_type": "username|mac|ip"}
# 200 OK
@app.route('/capport/sessions',methods = ['POST'] )
def post_sessions():
    ## get post data
    json_request = request.get_json(force=True)
    if (json_request is None):
        return (json.dumps({ "error": "invalid json payload" }), 400)

    ## get identity from
    if not ('identity' in json_request):
        return (json.dumps({ "error": "identity missing" }), 400)
    else:
        identity = json_request['identity']

    ## get the type of identity
    if not ('id_type' in json_request):
        return (json.dumps({"error": "identity type missing"}), 400)
    else:
        id_type = json_request['id_type']

    ## only create a new session if the identity matches up with
    ## a session in the captive portal enforcer
    if (check_identity(identity, id_type) == False):
        return (json.dumps({"error": "client session not initiated"}), 400)

    ## create a new session for this identity
    session = model.session.newSession(json_request['identity'])
    if (session is None):
        return (json.dumps({ "error": "could not inititate session" }), 500)

    ## add some requirements

    ## add view requirement for terms & conditions
    terms = os.getenv( "CAPPORT_TERMS_URL", request.url_root+"terms")
    terms = terms+"?session="+session.getId()
    req = model.requirement.newRequirement( session.getId(),"view_page",terms)
    session.addRequirement(req)

    ## add provide_credentials requirement for login page
    login = os.getenv( "CAPPORT_LOGIN_URL", request.url_root+"login")
    login = login+"?session="+session.getId()
    req = model.requirement.newRequirement(session.getId(),"provide_credentials",login)
    session.addRequirement(req)

    ## set limits
    session.setExpire(time.time()+3600) # let's do 1 hour...
    session.setDataLimit(10000000) # let's do 10000000 bytes...

    session.setIdType(id_type)

    ## store in redis
    session.store()

    return session_status(session, 201)

# The session now exists, and GET works:
# GET http://<server>/capport/sessions/<session_uuid> (Accept: application/json)
# 200 OK
@app.route('/capport/sessions/<string:session_uuid>',methods = ['GET'] )
def get_sessions(session_uuid):
    ## TODO: session status as html not implemented in this example
    # if not request_wants_json():
    #    return app.send_static_file('index.html')

    ## load the session with given session id
    session = model.session.loadSession(session_uuid)
    if (session is None):
        return (json.dumps({ "error": "invalid session" }), 500)

    return session_status(session)


# When the client wants to explicitly leave the network, delete the href for the session:
# DELETE http://<server>/capport/sessions/<session_uuid>
# 204 no content
@app.route('/capport/sessions/<string:session_uuid>',methods = ['DELETE'] )
def delete_sessions(session_uuid):
    ## load the session with given session id
    session = model.session.loadSession(session_uuid)
    if (session is None):
        return (json.dumps({ "error": "invalid session" }), 500)

    ## logoff the chilli portal session
    logoff_identity(session.getIdentity(), session.getIdType())

    ## delete the session
    session.delete()

    ## return status 204 "no content" instead of 200 "ok"
    return ("", 204)


# When the client wants to use the API to login instead of using the login form
# POST http://<server>/capport/login/<string:session_uuid>
# 200 OK
@app.route('/capport/login/<string:session_uuid>', methods = ['POST'] )
def login_sessions(session_uuid):
    # Make sure the request is proper json
    json_request = request.get_json(force=True)
    if (json_request is None):
        return (json.dumps({"error": "invalid json payload"}), 400)

    ## load the session with given session_id
    session = model.session.loadSession(session_uuid)
    if (session is None):
        return (json.dumps({ "error": "invalid session" }), 500)

    ## retrieve the username
    if not ('username' in json_request):
        return (json.dumps({"error": "username missing"}), 400)
    else:
        username = json_request['username']

    ## retrieve the password
    if not ('password' in json_request):
        return (json.dumps({"error": "password missing"}), 400)
    else:
        password = json_request['password']

    mypass = password[::-1]
    if (username == password):
        enable_traffic(session.getIdentity(), session.getIdType())
    else:
        return (json.dumps({"error": "credentials provided are incorrect"}), 401)

    return session_status(session)



##################
## let's do it! ##
##################

if __name__ == "__main__":
    model.database.initDatabase()
    app.run(host="0.0.0.0")
