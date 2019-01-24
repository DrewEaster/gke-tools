FROM ubuntu:xenial

RUN apt-get update && apt-get install -y \
    apt-transport-https \
    ca-certificates \
    curl \
    software-properties-common \
    build-essential \
    python \
    python-pip

# Install Docker
RUN curl -fsSL https://download.docker.com/linux/ubuntu/gpg | apt-key add -    
RUN add-apt-repository \
   "deb [arch=amd64] https://download.docker.com/linux/ubuntu \
   xenial \
   stable"
RUN apt-get update
RUN apt-get install -y docker-ce

# Install gcloud SDK and kubectl
RUN apt-get install -y apt-transport-https
RUN echo "deb https://packages.cloud.google.com/apt cloud-sdk-xenial main" | tee -a /etc/apt/sources.list.d/google-cloud-sdk.list
RUN curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | apt-key add -
RUN apt-get update
RUN apt-get install -y google-cloud-sdk kubectl

# Install PyYaml
RUN pip install pyyaml

# Install Helm
RUN curl https://raw.githubusercontent.com/kubernetes/helm/master/scripts/get > get_helm.sh
RUN chmod 700 get_helm.sh
RUN ./get_helm.sh
RUN helm init --client-only 

COPY scripts /scripts

WORKDIR /code

ENTRYPOINT ["python", "/scripts/gke-tools.py"]