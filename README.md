## Version Control
Author: Kristaps Kulikovskis
Version: 1.0
Short Description: ConferenceCentral App for Udacity Full stack nano degree program project 4

## Products
- [App Engine][1]

## Language
- [Python][2]

## APIs
- [Google Cloud Endpoints][3]

## Setup Instructions
#### To access online API
1. To use the provided API, please go to https://udacity-kkproject4.appspot.com/_ah/api/explorer

#### To access on local machine
1. Update the value of application in app.yaml to the app ID you have registered in the App Engine admin console and would like to use to host your instance of this sample.
1. Update the values at the top of settings.py to reflect the respective client IDs you have registered in the Developer Console.
1. Update the value of CLIENT_ID in static/js/app.js to the Web client ID
1. Run the app with the devserver using dev_appserver.py DIR, and ensure it's running by visiting your local server's address (by default localhost:8080.)


##Session design choices
Session object has all the attributes required by instructions.
* sessionType is a repeated field since a session could be of several types
* highlights is a repeated field
* duration is a field where input is meant to be in full hours and then it is internally changed to seconds
* Speaker is a separate entity which has to be added when creating a Session entity. Separating speakers from sessions allows easier querying for speaker sessions
and makes caching more efficient
* Session is a child of Conference, which seems logical given that all Sessions must be related to a Conference
* date, startTime and duration properties are Date, Time, and Integer(seconds) properties so they can be used in filtering

##Additional queries

For the additional queries I chose to implement:
1. conferenceSessionByTime() - A user can find sessions for a particular conference that start before/after a specified time
1. sessionsInTimeLimitLessThanDuration() - A user can select earliest and latest starting time for a session and a maximum duration in hours.

##Inequality query problem

Since Datastore doesn't allow more than one attribute to be queried with inequality(because it is needed to order by that attribute first) it is not
possible to make a query to get all Sessions that are not workshops and are not after 7pm.

But there are two alternative solutions that I can think of:

####First approach#####

1. First query for all **Session keys** that are before 7pm.
1. After we have all the keys for those sessions we can make another query to get all the Sessions that are not "workshop" and whose keys are included
in the array that we got from the first query

This approach would work, but the drawback here is that it would make 2 requests and it will cost more and might be slower since we are connecting to the datastore
two times

####Second approach####

1. Query for all Sessions that are before 7pm.
1. make another variable that holds the array of sessions that we want to return
1. Iterate through each Session that we got from the first query and check if it is a "workshop".
1. Append it to the newly created array if it is not

This option is better than the first one because it only involves fetching data from the datastore once, so it is cheaper. Also processing data in python might
be faster than making two seperate query requests, which also reduces the latency.

