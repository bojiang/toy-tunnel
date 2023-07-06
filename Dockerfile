# prod-base
from debian:bullseye as prod-base
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3 1

COPY requirements.txt .
RUN pip3 install -r requirements.txt --user
RUN pip install --upgrade setuptools --user

RUN apt-get install -y --no-install-recommends curl wireguard

# prod
from prod-base as prod
ENV PATH=/root/.local/bin:$PATH
COPY . /root/project
WORKDIR /root/project
ENTRYPOINT []
CMD ["circusd" , "circus.ini"]

# distroless
from prod-base as distroless-source
COPY --from=ghcr.io/ahmetozer/distroless-helper /bin/distroless-helper /bin/distroless-helper
RUN /bin/distroless-helper /usr/bin/curl /opt

from gcr.io/distroless/python3-debian11:debug as distroless

COPY --from=distroless-source /root/.local /root/.local
COPY --from=distroless-source /opt/ /
ENV PATH=/root/.local/bin:$PATH

COPY . /root/project
WORKDIR /root/project
ENTRYPOINT []
CMD ["circusd" , "circus.ini"]
