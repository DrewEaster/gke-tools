#!/usr/bin/env python

import os
import errno
import shutil
import sys
import subprocess
import yaml

def bucket_uri_from_bucket_name(gcs_bucket_name):
    return 'gs://{0}'.format(gcs_bucket_name)

def sync_from_remote_repository(gcs_bucket_name, local_chart_repo_directory):
    print 'Syncing local chart repository from remote GCS bucket...'
    result = subprocess.call(['gsutil', 'rsync', '-d', bucket_uri_from_bucket_name(gcs_bucket_name), local_chart_repo_directory])
    if result != 0:
        raise ValueError('Failed to sync remote chart repository from GCS bucket {0}'.format(gcs_bucket_name))

def sync_to_remote_repository(gcs_bucket_name, local_chart_repo_directory):
    print 'Syncing local chart repository to remote GCS bucket...'
    result = subprocess.call(['gsutil', 'rsync', '-d', local_chart_repo_directory, bucket_uri_from_bucket_name(gcs_bucket_name)])
    if result != 0:
        raise ValueError('Failed to sync local chart repository to GCS bucket {0}'.format(gcs_bucket_name))

def generate_chart_yaml_file(chart_name, chart_version, chart_directory):
    print 'Generating Chart.yaml file...'
    chart_yaml_model = dict(
        name=chart_name,
        version=chart_version,
        appVersion=chart_version
    )
    
    try:
        with open(os.path.join(chart_directory, "Chart.yaml"), 'w') as outfile:
            yaml.dump(chart_yaml_model, outfile, default_flow_style=False)
    except IOError:
        raise ValueError('Failed to generate Chart.yaml file for new chart package')

def package_chart(chart_name, chart_version, chart_directory, local_chart_repo_directory):
    print 'Packaging chart...'
    generate_chart_yaml_file(chart_name, chart_version, chart_directory)
    result = subprocess.call(['helm', 'package', '-d', local_chart_repo_directory, chart_directory])
    if result != 0:
        raise ValueError('Failed to package new helm chart')

def copy_chart_source_to_temporary_charts_directory(chart_configuration, temporary_charts_directory):
    print 'Copying chart source files to temporary directory...'
    chart_name = chart_configuration['name']
    chart_source_directory = chart_configuration['src']
    temporary_chart_directory = os.path.join(temporary_charts_directory, chart_name)
    try:
        shutil.copytree(chart_source_directory, temporary_chart_directory)
        return temporary_chart_directory
    except shutil.Error as err:
        raise ValueError('Failed to copy chart source to temporary charts directory')

def reindex_local_chart_repo(local_chart_repo_directory, repo_url):
    print 'Reindexing local chart repository...'
    result = subprocess.call(['helm', 'repo', 'index', local_chart_repo_directory, '--url', repo_url])
    if result != 0:
        raise ValueError('Failed to reindex local chart repository')

def image_tag_from_docker_build_definition(docker_build_definition, version, project):
    return 'gcr.io/{0}/{1}:{2}'.format(project, docker_build_definition['name'], version)

def build_docker_image(docker_build_definition, version, project):
    image_tag = image_tag_from_docker_build_definition(docker_build_definition, version, project)
    command = ['docker', 'build', '-f', docker_build_definition['dockerfile'], '-t', image_tag]
    if docker_build_definition.get('add_gcs_creds') == 'true':
        command.extend(['-v', '/keys:/tmp/keys', '-e', 'GOOGLE_APPLICATION_CREDENTIALS=/tmp/keys/service-account-credentials.json' ])
    command.append(docker_build_definition['context'])
    result = subprocess.call(command)
    if result != 0:
        sys.exit(result)

def build_docker_images(build_configuration, version, project):
    for docker_build_definition in build_configuration['images']:
        build_docker_image(docker_build_definition, version, project)

def push_docker_images(build_configuration, version, project):
    for docker_build_definition in build_configuration['images']:
        image_tag = image_tag_from_docker_build_definition(docker_build_definition, version, project)
        result = subprocess.call(['gcloud', 'docker', '--', 'push', image_tag])
        if result != 0:
            sys.exit(result)

def generate_chart_package_path(chart_name, chart_version, local_chart_repo_directory):
    return os.path.join(local_chart_repo_directory, '{0}-{1}.tgz'.format(chart_name, chart_version))

def release_chart(chart_name, chart_version, environment_name, local_chart_repo_directory):
    chart_package_path = generate_chart_package_path(chart_name, chart_version, local_chart_repo_directory)
    
    values_files_directory_path = '/config/{0}'.format(environment_name)
    values_file_names = [f for f in os.listdir(values_files_directory_path) if os.path.isfile(os.path.join(values_files_directory_path, f))]
    
    helm_upgrade_command = [val for cmd_list in [['helm', 'upgrade', '-i']] + [['-f', os.path.join(values_files_directory_path, file_name)] for file_name in values_file_names] + [[chart_name, chart_package_path]] for val in cmd_list]

    result = subprocess.call(helm_upgrade_command)
    
    if result != 0:
        raise ValueError('Failed to release helm chart')
        
def load_configuration(filename):
    with open(filename, 'r') as stream:
        try:
            return yaml.load(stream)
        except yaml.YAMLError as exc:
            print(exc)

def authenticate_gcloud(project):
    result = subprocess.call(['gcloud', 'auth', 'activate-service-account', '--key-file', '/keys/service-account-credentials.json', '--project', project])
    if result != 0:
        sys.exit(result)

def authenticate_gcr(project):
    result = subprocess.call(['gcloud', 'docker', '--authorize-only', '--project', project])
    if result != 0:
        sys.exit(result)

def configure_kubectl(project, zone, cluster_name):
    result = subprocess.call(['gcloud', 'container', 'clusters', 'get-credentials', cluster_name, '--project', project, '--zone', zone])
    if result != 0:
        sys.exit(result)

def gke_configuration_for_environment(environment_name, build_configuration):
    environments = build_configuration['release']['environments']
    environment = next(e for e in environments if e['name'] == environment_name)
    return environment['gke']

def make_directory(directory):
    try:
        os.makedirs(directory)
    except OSError as err:
        if err.errno != errno.EEXIST:
            raise ValueError('Directory {0} does not exist and could not be created'.format(directory))

def remove_directory(directory):
    try:
        print 'Removing directory {0} ...'.format(directory)
        shutil.rmtree(directory)
    except shutil.Error as err:
        print 'Directory {0} could not be removed'.format(directory)

def build(version, build_configuration, project):
    build_docker_images(build_configuration['build'], version, project)
    push_docker_images(build_configuration['build'], version, project)

def package(chart_version, build_configuration):
    local_chart_repo_directory = '~/.gke-tools/helm/repository/'
    temporary_charts_directory = '~/.gke-tools/helm/charts/'

    # Make sure local chart repository directory is created
    make_directory(local_chart_repo_directory)

    try:
        # Fetch chart configuration
        chart_configuration = build_configuration['build']['chart']

        # Fetch remote repository config
        gcs_bucket_name = chart_configuration['repository']['bucket-name']
        chart_repository_url = chart_configuration['repository']['url']

        # Sync remote chart repository from Google Cloud Storage
        sync_from_remote_repository(gcs_bucket_name, local_chart_repo_directory)

        # Copy chart source to temporary chart directory and maintain reference to the newly created temporary directory
        chart_directory = copy_chart_source_to_temporary_charts_directory(chart_configuration, temporary_charts_directory)

        # Package new chart from temporary chart directory
        package_chart(chart_configuration['name'], chart_version, chart_directory, local_chart_repo_directory)

        # Reindex local chart repository
        reindex_local_chart_repo(local_chart_repo_directory, chart_repository_url)

        # Sync local chart repository to Google Cloud Storage
        sync_to_remote_repository(gcs_bucket_name, local_chart_repo_directory)
    finally:
        # Clean up temporary directories
        remove_directory(local_chart_repo_directory)
        remove_directory(temporary_charts_directory)

def release(chart_version, environment_name, build_configuration):
    # Fetch chart configuration
    chart_configuration = build_configuration['build']['chart']
    chart_name = chart_configuration['name']
    
    # Fetch remote repository config
    gcs_bucket_name = chart_configuration['repository']['bucket-name']

    local_chart_repo_directory = '~/.gke-tools/helm/repository/'

    # Make sure local chart repository directory is created
    make_directory(local_chart_repo_directory)

    try:
        # Sync remote chart repository from Google Cloud Storage
        sync_from_remote_repository(gcs_bucket_name, local_chart_repo_directory)

        # Release the chart
        release_chart(chart_name, chart_version, environment_name, local_chart_repo_directory)
    finally:
        # Clean up temporary directory
        remove_directory(local_chart_repo_directory)

def run_script():
    cmd = sys.argv[1]
    version = sys.argv[2]
    build_configuration = load_configuration('build-and-release.yaml')

    if cmd == 'build':
        project = build_configuration['build']['gcr']['project']
        
        # Authenticate
        authenticate_gcloud(project)
        authenticate_gcr(project)

        # Build and package
        build(version, build_configuration, project)
        package(version, build_configuration)

    elif cmd == 'release':
        environment_name = sys.argv[3]
        
        # Extract environment configuration
        gke_configuration = gke_configuration_for_environment(environment_name, build_configuration)
        project = gke_configuration['project']
        cluster_name = gke_configuration['cluster-name']
        zone = gke_configuration['zone']

         # Authenticate
        authenticate_gcloud(project)
        authenticate_gcr(project)
        configure_kubectl(project, zone, cluster_name)

        # TODO: Install Tiller if not already installed

        # Release
        release(version, environment_name, build_configuration)

    else:
        raise ValueError('Unrecognised cmd. Must be \'build\' or \'release\'')

run_script()
