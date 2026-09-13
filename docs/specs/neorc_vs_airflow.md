

In Airflow state is directly managed by the scheduler, in Neorc there is the manager keeping the state, the scheduler queries the manager for new work needing scheduling actions.


Airflow DAGs as code require deploying python code to all components, the scheduler, the workers, the web.
Neorc manage flow definitions as data, code is only deployed to workers. Each worker can have a subset of the code.
Workers in more than one programming language can be implemented.

Airflow does not natively supports cycles. Neorc supports simple "while X do Y" loops.

Airflow is (or was originally) oriented to batch processes where each task handle multiple "rows" or instances of the same, like "let's ingest and process data about all the last month bookings". Neorc is more oriented to a single flow run per event, like "one room booking is one flow run". 
In that sense Neorc start with connecting tasks inputs and outputs in its first version. While in Airflow that was secondary, supported by xcomps, and recently improved with an annotations API. 
The difference is diffuse though, and both can be used in any of the cases.

Airflow ecosystem is huge, with multiple connectors or providers, it provides a bigger turn-key solution. Neorc aims to have minimal dependencies and be a flexible tool that can be forked, integrated and extended with ease.

Your code lives __inside__ Airflow. Neorc tries to not touch your code but rather be __around it__ so your classes and functions can run anywhere, are more easily testable or ported to other orchestration tools when needed.