App Engine application for the Udacity training course.

## Products
- [App Engine][1]

## Language
- [Python][2]

## APIs
- [Google Cloud Endpoints][3]

## Setup Instructions
1. Update the value of `application` in `app.yaml` to the app ID you
   have registered in the App Engine admin console and would like to use to host
   your instance of this sample.
1. Update the values at the top of `settings.py` to
   reflect the respective client IDs you have registered in the
   [Developer Console][4].
1. Update the value of CLIENT_ID in `static/js/app.js` to the Web client ID
1. (Optional) Mark the configuration files as unchanged as follows:
   `$ git update-index --assume-unchanged app.yaml settings.py static/js/app.js`
1. Run the app with the devserver using `dev_appserver.py DIR`, and ensure it's running by visiting your local server's address (by default [localhost:8080][5].)
1. (Optional) Generate your client library(ies) with [the endpoints tool][6].
1. Deploy your application.

##Inequality query problem
Since Datastore doesn't allow more than one attribute to be queried with inequality(because it is needed to order by that attribute first) it is not
possible to make a query to get all Sessions that are not workshops and are not after 7pm.

But there are two alternative solutions that I can think of:

1.1. First query for all **Session keys** that are before 7pm.
1.2. After we have all the keys for those sessions we can make another query to get all the Sessions that are not "workshop" and whose keys are included
in the array that we got from the first query

This approach would work, but the drawback here is that it would make 2 requests and it will cost more and might be slower since we are connecting to the datastore
two times

2.1. Query for all Sessions that are before 7pm.
2.2. make another variable that holds the array of sessions that we want to return
2.3. Itterate through each Session that we got from the first query and check if it is a "workshop".
2.4. Append it to the newly created array if it is not

This option is better than the first one because it only involves fetching data from the datastore once, so it is cheaper. Also processing data in python might
be faster than making two seperate query requests, which also reduces the latency.


[1]: https://developers.google.com/appengine
[2]: http://python.org
[3]: https://developers.google.com/appengine/docs/python/endpoints/
[4]: https://console.developers.google.com/
[5]: https://localhost:8080/
[6]: https://developers.google.com/appengine/docs/python/endpoints/endpoints_tool
