#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


from datetime import datetime

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import StringMessage
from models import BooleanMessage
from models import Session
from models import Speaker
from models import SpeakerForm
from models import SpeakerForms
from models import SessionForm
from models import SessionForms
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import TeeShirtSize

from settings import WEB_CLIENT_ID
from settings import ANDROID_CLIENT_ID
from settings import IOS_CLIENT_ID
from settings import ANDROID_AUDIENCE

from utils import getUserId

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
ANNOUNCEMENT_TPL = ('Last chance to attend! The following conferences '
                    'are nearly sold out: %s')
# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": [ "Default", "Topic" ],
}

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS =    {
            'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
            }

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
    sessionType=messages.StringField(2),
    time=messages.StringField(3),
    direction=messages.StringField(4)
)
FEATURED_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey =messages.StringField(1)
)


CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

SESS_POST_REQUEST = endpoints.ResourceContainer(
    SessionForm,
    websafeConferenceKey=messages.StringField(1),
)

SESS_SP_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSpeakerKey=messages.StringField(1)
)

WISH_POST_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSessionKey=messages.StringField(1)
)
Q_PROB_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    duration=messages.StringField(1),
    time_earliest=messages.StringField(2),
    time_latest=messages.StringField(3)
)
FEATURED_S_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    speaker_key= messages.StringField(1),
    conference_key = messages.StringField(2)
)

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


@endpoints.api(name='conference', version='v1', audiences=[ANDROID_AUDIENCE],
    allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID, ANDROID_CLIENT_ID, IOS_CLIENT_ID],
    scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

    # - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf


    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
                              'conferenceInfo': repr(request)},
                      url='/tasks/send_confirmation_email'
                      )
        return request


    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                    'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                    'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
                      http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)


    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
                      path='conference/{websafeConferenceKey}',
                      http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)


    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
                      path='conference/{websafeConferenceKey}',
                      http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                    'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='getConferencesCreated',
                      http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
                items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )


    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)


    @endpoints.method(ConferenceQueryForms, ConferenceForms,
                      path='queryConferences',
                      http_method='POST',
                      name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
                items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in \
                       conferences]
        )


    # - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                    key = p_key,
                    displayName = user.nickname(),
                    mainEmail= user.email(),
                    teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        #if field == 'teeShirtSize':
                        #    setattr(prof, field, str(val).upper())
                        #else:
                        #    setattr(prof, field, val)
                        prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
                      path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()


    @endpoints.method(ProfileMiniForm, ProfileForm,
                      path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


    # - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
                Conference.seatsAvailable <= 5,
                Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = ANNOUNCEMENT_TPL % (
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement


    @endpoints.method(message_types.VoidMessage, StringMessage,
                      path='conference/announcement/get',
                      http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        return StringMessage(data=memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY) or "")


    # - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                    'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                        "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                        "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='conferences/attending',
                      http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser() # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) \
                                      for conf in conferences]
                               )


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
                      path='conference/{websafeConferenceKey}',
                      http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
                      path='conference/{websafeConferenceKey}',
                      http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='filterPlayground',
                      http_method='GET', name='filterPlayground')
    def filterPlayground(self, request):
        """Filter Playground"""
        q = Conference.query()
        # field = "city"
        # operator = "="
        # value = "London"
        # f = ndb.query.FilterNode(field, operator, value)
        # q = q.filter(f)
        q = q.filter(Conference.city=="London")
        q = q.filter(Conference.topics=="Medical Innovations")
        q = q.filter(Conference.month==6)

        return ConferenceForms(
                items=[self._copyConferenceToForm(conf, "") for conf in q]
        )

    # - - - Sessions objects - - - - - - - - - - - - - - - - -

    def _createSessionObject(self, request):
        """Create a new session for a conference"""
        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf_key = ndb.Key(urlsafe=wsck)
        conf = conf_key.get()
        if not conf:
            raise endpoints.NotFoundException(
                    'No conference found with key: %s' % wsck)
        #check if user is the organizer of the conference
        profile = self._getProfileFromUser()
        org_check = Conference.query(ancestor=profile.key).fetch()
        if conf not in org_check:
            raise endpoints.BadRequestException("Only the Organizer can create sessions")

        # copy SessionForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeConferenceKey']
        #create session id and key
        s_id = Session.allocate_ids(size=1, parent=conf_key)[0]
        s_key = ndb.Key(Session, s_id, parent=conf_key)

        #Check if the Speaker Entity exists
        speaker = ndb.Key(urlsafe=data['speakerId']).get()
        if not speaker:
            raise endpoints.BadRequestException("Speaker ID is not valid")

        #Assign keys
        data['key'] = s_key
        data['conferenceId'] = wsck
        del data['websafeKey']

        #Convert Date and Time attributes
        if data['date']:
            data['date'] = datetime.strptime(data['date'], "%Y-%m-%d").date()

        if data['startTime']:
            data['startTime'] = datetime.strptime(data['startTime'], "%H:%M").time()
        #convert duration to seconds
        if data['duration']:
            data['duration'] *= 60 * 60

        Session(**data).put()

        # Memcache featured speakers
        taskqueue.add(params={'speaker_key': data['speakerId'],
                              'conference_key': wsck},
                      url='/tasks/set_featured_speaker'
                      )
        return self._copySessionToForm(ndb.Key(Session, s_id, parent=conf_key).get())

    def _copySessionToForm(self, session):
        """Copy relevant fields from Session to SessionForm."""
        sf = SessionForm()
        for field in sf.all_fields():
            if hasattr(session, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('date') or field.name.endswith('Time'):
                    setattr(sf, field.name, str(getattr(session, field.name)))
                else:
                    setattr(sf, field.name, getattr(session, field.name))
            elif field.name == "websafeKey":
                setattr(sf, field.name, session.key.urlsafe())
            elif field.name == "speakerName":
                speaker = ndb.Key(urlsafe=session.speakerId).get()
                setattr(sf, field.name, speaker.name)

        sf.check_initialized()
        return sf

    @endpoints.method(CONF_GET_REQUEST, SessionForms,
                      path='conference/{websafeConferenceKey}/sessions',
                      http_method='GET', name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Return requested sessions for a conference (by websafeConferenceKey)."""
        wsck = request.websafeConferenceKey
        conf_key = ndb.Key(urlsafe=wsck)
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                    'No conference found with key: %s' % wsck)
        # fetch sessions
        sessions = Session.query(Session.conferenceId == wsck)
        return SessionForms(items=[self._copySessionToForm(session) for session in sessions])



    @endpoints.method(CONF_GET_REQUEST, SessionForms,
                      path='conference/{websafeConferenceKey}/sessions_by_type',
                      http_method='GET', name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """Return requested sessions for a conference (by websafeConferenceKey) with specific type."""
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                    'No conference found with key: %s' % wsck)
        # fetch sessions filtered by sessionType
        sessions = Session.query(Session.conferenceId == wsck and Session.sessionType == request.sessionType)
        return SessionForms(items=[self._copySessionToForm(session) for session in sessions])


    @endpoints.method(SESS_SP_GET_REQUEST, SessionForms,
                      path='sessions/{websafeSpeakerKey}', http_method='GET',
                      name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """Return all sessions with a specific speaker."""
        wssk = request.websafeSpeakerKey
        sessions = Session.query(Session.speakerId == wssk)

        return SessionForms(items=[self._copySessionToForm(session) for session in sessions])


    @endpoints.method(SESS_POST_REQUEST, SessionForm,
                      path='session/{websafeConferenceKey}',
                      http_method='POST',name='createSession')
    def createSession(self, request):
        """Create new session."""
        return self._createSessionObject(request)

     # - - - Speaker methods - - - - - - - - - - - - - - - - -

    def _createSpeakerObject(self, request):
        s_id = Speaker.allocate_ids(size=1)[0]
        s_key = ndb.Key(Speaker, s_id)
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        if not data['name']:
            raise endpoints.BadRequestException("Speaker 'name' field required")
        del data['websafeKey']
        data['key'] = s_key
        Speaker(**data).put()

        return self._copySpeakerToForm(ndb.Key(Speaker, s_id).get())

    def _copySpeakerToForm(self, speaker):
        sf = SpeakerForm()
        for field in sf.all_fields():
            if hasattr(speaker, field.name):
                # convert Date to date string; just copy others
                setattr(sf, field.name, getattr(speaker, field.name))
            elif field.name == "websafeKey":
                setattr(sf, field.name, speaker.key.urlsafe())

        sf.check_initialized()
        return sf

    @endpoints.method(message_types.VoidMessage, SpeakerForms, path='speakers',
                      http_method='GET', name='getSpeakers')
    def getSpeakers(self, request):
        """Return all speakers available"""
        speakers = Speaker.query()

        return SpeakerForms(items=[self._copySpeakerToForm(speaker) for speaker in speakers])


    @endpoints.method(SpeakerForm, SpeakerForm,
                      path='speaker', http_method='POST',
                      name='createSpeaker')
    def createSpeaker(self, request):
        """Create a Speaker entity"""
        return self._createSpeakerObject(request)


    #  # - - - Wishlist methods - - - - - - - - - - - - - - - - -

    @ndb.transactional()
    def _appendToWishlist(self, request):
        """Add session to users wishlist."""
        profile = self._getProfileFromUser()
        retval = None
        error_message= ""

        # check if session exists given websafeSessionKey
        # get session; check that it exists
        wssk = request.websafeSessionKey
        session = ndb.Key(urlsafe=wssk).get()
        if not session:
            raise endpoints.NotFoundException(
                    'No session found with key: %s' % wssk)
        if wssk in profile.wishlist:
            retval = False
            error_message = "You already have this session in your wishlist"
        else:
            profile.wishlist.append(wssk)
            profile.put()
            retval=True

        return BooleanMessage(data=retval, message=error_message)

    @ndb.transactional()
    def _removeFromWishlist(self, request):
        """Remove session from users wishlist."""
        retval= None
        error_message=""
        profile = self._getProfileFromUser()
        wssk = request.websafeSessionKey
        # Check if session exists
        session = ndb.Key(urlsafe=wssk).get()
        if not session:
            raise endpoints.NotFoundException(
                    'No session found with key: %s' % wssk)

        if wssk not in profile.wishlist:
            retval = False
            error_message = "This session was not in your wishlist"
        else:
            profile.wishlist.remove(wssk)
            profile.put()
            retval=True

        return BooleanMessage(data=retval, message=error_message)

    @endpoints.method(WISH_POST_REQUEST, BooleanMessage,
                      path='session/{websafeSessionKey}/wishlist',
                      http_method='POST', name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        return self._appendToWishlist(request)


    @endpoints.method(message_types.VoidMessage, SessionForms,
                      path='user/wishlist', http_method='GET', name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """Get sessions from users wishlist."""
        profile = self._getProfileFromUser()
        session_keys = [ndb.Key(urlsafe=wssk) for wssk in profile.wishlist]
        sessions = ndb.get_multi(session_keys)
        return SessionForms(items=[self._copySessionToForm(session) for session in sessions])



    @endpoints.method(WISH_POST_REQUEST, BooleanMessage,
                      path='user/wishlist/{websafeSessionKey}/remove', http_method='DELETE',
                      name='deleteSessionInWishlist')
    def deleteSessionInWishlist(self, request):
        return self._removeFromWishlist(request)

    #Additional queries
    @endpoints.method(CONF_GET_REQUEST, SessionForms,
                      path='conference/{websafeConferenceKey}/sessions_by_time',
                      http_method='GET', name='conferenceSessionsByTime')
    def conferenceSessionsByTime(self, request):
        """returns all conference sessions that start before or after a given time"""

        wsck = request.websafeConferenceKey
        time = request.time
        direction = request.direction
        if direction == "before":
            operator = "<="
        elif direction == "after":
            operator = ">="
        else:
            raise endpoints.BadRequestException("direction can only be 'before' or 'after'")

        formatted_query = ndb.query.FilterNode("startTime", operator, datetime.strptime(time, "%H:%M"))
        sessions = Session.query()\
            .order(Session.startTime)\
            .filter(formatted_query)\
            .filter(Session.conferenceId == wsck)
        return SessionForms(items=[self._copySessionToForm(session) for session in sessions])

    #Solution to the query problem
    @endpoints.method(Q_PROB_REQUEST, SessionForms,
                      path='sessions/query_problem_solved',
                      http_method='GET',
                      name='sessionsInTimeLimitLessThanDuration')
    def sessionsInTimeLimitLessThanDuration(self, request):
        """Returns all sessions that are in a certain time limit and does not exceed some duration."""
        sessions_unfiltered = Session.query()\
            .order(Session.startTime)\
            .filter(Session.startTime >= datetime.strptime(request.time_earliest, "%H:%M").time())\
            .filter(Session.startTime <= datetime.strptime(request.time_latest, "%H:%M").time())
        sessions = []
        #convert duration in seconds
        duration_in_seconds = 60*60*request.duration

        for session in sessions_unfiltered:
            if duration_in_seconds > session.duration:
                sessions.append(session)
        return SessionForms(items=[self._copySessionToForm(session) for session in sessions])

    # - - - Memcache Featured Speakers ----------

    @staticmethod
    def _cacheFeaturedSpeaker(websafeConferenceKey, websafeSpeakerKey):
        """Check if speaker is in two or more sessions at a given conference and add an entry to Memcache if true"""
        conf = ndb.Key(urlsafe=websafeConferenceKey).get()
        speaker = ndb.Key(urlsafe=websafeSpeakerKey).get()
        speaker_sessions = Session.query(Session.speakerId == websafeSpeakerKey and
                                         Session.conferenceId == websafeConferenceKey)
        if speaker_sessions.count() >= 2:
            print (session.name for session in speaker_sessions)
            string = speaker.name +":"+ (', '.join(session.name for session in speaker_sessions))
            memcache.set(websafeConferenceKey, string)
        return string

    @endpoints.method(FEATURED_GET_REQUEST, StringMessage,
                      path='{websafeConferenceKey}/get_featured_speaker',
                      http_method='GET', name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """Return featured speaker from Memcache for a conference"""
        return StringMessage(data=memcache.get(request.websafeConferenceKey) or "")


api = endpoints.api_server([ConferenceApi]) # register API

