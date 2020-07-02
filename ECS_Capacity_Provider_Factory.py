import os
import sys
import time
import shutil
import logging
import subprocess

DepDir = './Requests'
sys.path.insert(0, DepDir)
DepDir = './Boto3'
# DepDir = '/tmp/DipDir/Boto3'
# os.makedirs(DepDir, exist_ok=True)
# subprocess.run(["pip", "install", "-t", DepDir, "boto3"])
sys.path.insert(0, DepDir)

import boto3
#import cfnresponse

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def CreateECSService(Action, responseData):
    try:
        CapacityProvider = responseData['CapacityProviderARN']
    except KeyError:
        CapacityProvider = ''

    Cluster = os.environ["ECSCLUSTER"]
    TaskDefinition = os.environ["TASKDEFINITION"]
    DesiredCount = os.environ["DESIREDCOUNT"]
    # CapacityProvider = os.environ["CAPACITYPROVIDERARN"]
    TagetGroup = os.environ["TARGETGROUPARN"]
    ContainerPort = os.environ["CONTAINERPORT"]
    TargetGroupArn = os.environ["TARGETGROUPARN"]

    LambdaName = os.environ["AWS_LAMBDA_FUNCTION_NAME"]

    ECSServiceName = 'ServiceECS-' + LambdaName
    ScalingPolicyName = 'ScalingPolicyECS-' + LambdaName # Change name to use ECS Service Name
    ServiceResourceId = 'service/' + Cluster + '/' + ECSServiceName

    ECSClient = boto3.client('ecs')
    AppASClient = boto3.client('application-autoscaling')

    if Action == 'Create':
        time.sleep(10)
        Response = ECSClient.create_service(
            cluster=Cluster,
            serviceName=ECSServiceName,
            taskDefinition=TaskDefinition,
            desiredCount=int(DesiredCount),
            capacityProviderStrategy=[{
                'capacityProvider': CapacityProvider,
                'weight': 1,
                'base': 1
            }],
            placementStrategy=[
                {'type': 'spread', 'field': 'attribute:ecs.availability-zone'},
                {'type': 'binpack', 'field': 'cpu'}
            ],
            healthCheckGracePeriodSeconds=2,
            schedulingStrategy='REPLICA',
            loadBalancers=[{
                'targetGroupArn': TagetGroup,
                'containerName': 'Instance-Info',
                'containerPort': int(ContainerPort)
            }]
        )
        print("Create Service AutoScaling API Response: {}".format(Response))
        responseData['ECSServiceName'] = Response['service']['serviceName']

        Status = 'NotActive'
        for x in range(12):
            if Status != 'ACTIVE':
                response = ECSClient.describe_services(
                                cluster=Cluster,
                                services=[ECSServiceName]
                            )
                Status = response['services'][0]['status']
                time.sleep(10)
            else:
                break
        if Status != 'ACTIVE':
            print(
                "ECS Service {} couldn't transition to ACTIVE state in 120 sec. Aborting Scling configuration for the service."
                .format(Response['service']['serviceName'])
            )
            return responseData

        # ResourceLabel = TargetGroupArn.split(':').pop()
        Response  = AppASClient.register_scalable_target(
                        ServiceNamespace='ecs',
                        ResourceId=ServiceResourceId,
                        ScalableDimension='ecs:service:DesiredCount',
                        MinCapacity=1,
                        MaxCapacity=30
                    )
        print("Register Scalable Target API Response: {}".format(Response))

        Response = AppASClient.put_scaling_policy(
            PolicyName=ScalingPolicyName,
            ServiceNamespace='ecs',
            ResourceId=ServiceResourceId,
            ScalableDimension='ecs:service:DesiredCount',
            PolicyType='TargetTrackingScaling',
            TargetTrackingScalingPolicyConfiguration={
                'TargetValue': 70,
                'PredefinedMetricSpecification': {
                    'PredefinedMetricType': 'ECSServiceAverageCPUUtilization'
                },
                'ScaleOutCooldown': 300,
                'ScaleInCooldown': 300,
                'DisableScaleIn': False
            }
        )
        print("Put Application AutoScaling API Response: {}".format(Response))
        responseData['ECSServiceScalingPolicy'] = Response['PolicyARN']

        return responseData

    if Action == 'Delete':
        # Update Service Desired Count to 0
        ECSServices = ECSClient.list_services(cluster=Cluster)
        for ECSService in ECSServices['serviceArns']:
            response = ECSClient.update_service(
                    cluster=Cluster,
                    service=ECSService,
                    desiredCount=0
            )
            if response['ResponseMetadata']['HTTPStatusCode'] == 200:
                print("Service \"{}\" updated successfully".format(ECSService))
            else:
                print("Failed to update service \"{}\"".format(ECSService))
                print(response)
        time.sleep(10)

        # Delete Application AutoScaling
        response  = AppASClient.delete_scaling_policy(
                        PolicyName=ScalingPolicyName,
                        ServiceNamespace='ecs',
                        ResourceId=ServiceResourceId,
                        ScalableDimension='ecs:service:DesiredCount'
                    )
        print("Delete Application AutoScaling Policy API status: {}".format(response))

        response  = AppASClient.deregister_scalable_target(
                        ServiceNamespace='ecs',
                        ResourceId=ServiceResourceId,
                        ScalableDimension='ecs:service:DesiredCount'
                    )
        print("Delete Application AutoScaling Scalable Target API status: {}".format(response))

        # Delete Services
        for ECSService in ECSServices['serviceArns']:
            response = ECSClient.delete_service(
                    cluster=Cluster,
                    service=ECSService,
                    force=True
            )
            if response['ResponseMetadata']['HTTPStatusCode'] == 200:
                print("Service \"{}\" stopped successfully".format(ECSService))
            else:
                print("Failed to stop service \"{}\"".format(ECSService))
                print(response)

        # Delete Capacity Providers
        time.sleep(10)

        # Stop any Tasks which is still running in the cluster
        ECSTasks = ECSClient.list_tasks(cluster=Cluster)
        for Task in ECSTasks['taskArns']:
            response = ECSClient.stop_task(cluster=Cluster, task=Task)
            if response['ResponseMetadata']['HTTPStatusCode'] == 200:
                print("Task \"{}\" deleted".format(Task))
            else:
                print("Couldn't delete task \"{}\"".format(Task))
                print(response)

        # Delete Capacity Provider
        ECSClusters = ECSClient.describe_clusters(clusters=[Cluster])
        for ECSCluster in ECSClusters['clusters']:
            for CP in ECSCluster['capacityProviders']:
                response = ECSClient.delete_capacity_provider(capacityProvider=CP)
                if response['ResponseMetadata']['HTTPStatusCode'] == 200:
                    print("Capacity Provider \"{}\" deleted successfully".format(CP))
                else:
                    print("Couldn't delete Capacity Provider \"{}\"".format(CP))
                    print(response)

    return responseData

def SignalCFN(event, context, Status, responseData, ResourceId):
    DepDir = './CfnResponse'
    # DepDir = '/tmp/DipDir/CfnResponse'
    # os.makedirs(DepDir, exist_ok=True)
    # subprocess.run(["pip", "install", "-t", DepDir, "cfnresponse"])
    sys.path.insert(0, DepDir)
    import cfnresponse
    cfnresponse.send(event, context, getattr(cfnresponse, Status), responseData, ResourceId)

def Get_ASG_Detail(AutoScalingGroups):
    ASGARN = ''
    for ASG in AutoScalingGroups["AutoScalingGroups"]:
        ASGARN = ASG["AutoScalingGroupARN"]
        break
    return ASGARN

def ASG_And_Instance_ScaleIn(AutoScalingClient, AutoScalingGroups, Status, ASG_NAME):
    # Expected value of Enable should be True or False
    for ASG in AutoScalingGroups["AutoScalingGroups"]:
        for Instance in ASG["Instances"]:
            if Instance["ProtectedFromScaleIn"] == (not Status):
                response = AutoScalingClient.set_instance_protection(
                    InstanceIds=[Instance["InstanceId"]],
                    AutoScalingGroupName=ASG["AutoScalingGroupName"],
                    ProtectedFromScaleIn=Status
                )
                print("API response of \"Instance Protection from ScaleIn\" {}:  {}".format(ASG["Instances"], response))
                logger.info("API response of \"Instance Protection from ScaleIn\" {}:  {}".format(ASG["Instances"], response))
    response = AutoScalingClient.update_auto_scaling_group(
                AutoScalingGroupName=ASG_NAME,
                NewInstancesProtectedFromScaleIn=Status
            )
    print("API response of \"Protection from ScaleIn\" {}:  {}".format(ASG_NAME, response))
    logger.info("API response of \"Protection from ScaleIn\" {}:  {}".format(ASG_NAME, response))

def Create_ECS_ASG_Capacity_Provider(ECS_CLUSTER_NAME, ASG_NAME, ASGARN):
    TARGETCAPACITY = os.environ["TARGETCAPACITY"]
    MAXIMUMSCALINGSTEPSIZE = os.environ["MAXIMUMSCALINGSTEPSIZE"]
    ECSClient = boto3.client("ecs")
    ECS_CP_Name = "Capacity-Provider-" + ASG_NAME
    CapPro = ECSClient.create_capacity_provider(
                name=ECS_CP_Name,
                autoScalingGroupProvider={"autoScalingGroupArn": ASGARN,
                    "managedTerminationProtection": "ENABLED",
                    "managedScaling": {"status": "ENABLED",
                        "targetCapacity": int(TARGETCAPACITY),
                        "minimumScalingStepSize": 1,
                        "maximumScalingStepSize": int(MAXIMUMSCALINGSTEPSIZE)
                    }
                }
            )
    print("Capacity Provider creation status :  {}".format(CapPro))
    logger.info("Capacity Provider creation status :  {}".format(CapPro))

    response = ECSClient.put_cluster_capacity_providers(
                cluster=ECS_CLUSTER_NAME,
                capacityProviders=[CapPro["capacityProvider"]["name"]],
                defaultCapacityProviderStrategy=[{
                    "capacityProvider": CapPro["capacityProvider"]["name"],
                    "weight": 1,
                    "base": 1
                }]
            )
    print("Assigned Capacity Provider to Cluster {}:  {}".format(ECS_CLUSTER_NAME, response))
    logger.info("Assigned Capacity Provider to Cluster {}:  {}".format(ECS_CLUSTER_NAME, response))
    #return CapPro["capacityProvider"]["capacityProviderArn"]
    return CapPro["capacityProvider"]["name"]

def lambda_handler(event, context):
    logger.info("Received Event {}".format(event))
    print("Received Event {}".format(event))
    print("Lambda Environment: {}".format(os.environ))
    responseData = {}
    LmabdaName = os.environ['AWS_LAMBDA_FUNCTION_NAME']

    try:
        ASG_NAME = event["ResourceProperties"]["ASG_NAME"]
        ECS_CLUSTER_NAME = event["ResourceProperties"]["ECS_CLUSTER_NAME"]
        print("Gathered inputs from Event.")
        #ASG_NAME = os.environ["ASGNAME"]
        #ECS_CLUSTER_NAME = os.environ["ECSCLUSTER"]
    except KeyError:
        Message = "No parameter ASGNAME/ECSCLUSTER found. Aborting script execution."
        print(Message)
        logger.info("AbortExecutionMessage {}".format(Message))
        raise

    try:
        AutoScalingClient = boto3.client("autoscaling")
        AutoScalingGroups = AutoScalingClient.describe_auto_scaling_groups(AutoScalingGroupNames=[ASG_NAME])
        ASGARN = Get_ASG_Detail(AutoScalingGroups)
        print("AutoScaling Describe complete: {}".format(AutoScalingGroups))
        print("ASG ARN: {}".format(ASGARN))
    except:
        print("Unexpected error occurred while creating ASG boto3 Client or querying ASG.")
        logger.info("Unexpected error occurred while creating ASG boto3 Client or querying ASG. Posting FAILED in cfnresponse")
        SignalCFN(event, context, 'FAILED', responseData, LmabdaName)
        raise

    try:
        if event["RequestType"] == "Create":
            if not str(ASGARN):
                print("No ASG Found with name " + ASG_NAME + ". Aborting Script Execution.")
                logger.info("Script Execution FAILED. Posting FAILED in cfnresponse")
                SignalCFN(event, context, 'FAILED', responseData, LmabdaName)
                return
            ASG_And_Instance_ScaleIn(AutoScalingClient, AutoScalingGroups, True, ASG_NAME)
            responseData['CapacityProviderARN'] = Create_ECS_ASG_Capacity_Provider(ECS_CLUSTER_NAME, ASG_NAME, ASGARN)
            # responseData['ECSServiceName'] = CapacityProviderARN(event["RequestType"], responseData['CapacityProviderARN')
            responseData = CreateECSService(event["RequestType"], responseData)

        elif event["RequestType"] == "Delete": # delete / update
            ASG_And_Instance_ScaleIn(AutoScalingClient, AutoScalingGroups, False, ASG_NAME)
            responseData = CreateECSService(event["RequestType"], responseData)

            # There is no API to Delete ECS Capacity Providers yet.

        else:
            pass
    except:
        print("Unexpected error occurred.")
        logger.info("Unexpected error occurred. Posting FAILED in cfnresponse")
        SignalCFN(event, context, 'FAILED', responseData, LmabdaName)
        raise

    print("Script Execution Completed Successfully. Posting SUCCESS in cfnresponse")
    print("Capacity Provider Name: {}".format(responseData))
    logger.info("Script Execution Completed Successfully. Posting SUCCESS in cfnresponse")
    SignalCFN(event, context, 'SUCCESS', responseData, LmabdaName)


# event = {"RequestType": "Create", "ServiceToken": "arn:aws:lambda:us-east-1:342241566140:function:CustomResource10-ASGCapacityProviderCreatorFunctio-1A0YGN2SCJAEY", "ResponseURL": "https://cloudformation-custom-resource-response-useast1.s3.amazonaws.com/arn%3Aaws%3Acloudformation%3Aus-east-1%3A342241566140%3Astack/CustomResource10/3b8f89a0-7126-11ea-aaf3-0e1ad0c392f9%7CASGCapacityProviderCreator%7C85842548-131d-4b3c-b79d-acb6ec4470b3?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Date=20200328T185944Z&X-Amz-SignedHeaders=host&X-Amz-Expires=7200&X-Amz-Credential=AKIA6L7Q4OWTROUDBTSZ%2F20200328%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Signature=49ea81a02fc4c35b6297be97a8effb962662a023a8f028887f159dc95c2f2a01", "StackId": "arn:aws:cloudformation:us-east-1:342241566140:stack/CustomResource10/3b8f89a0-7126-11ea-aaf3-0e1ad0c392f9", "RequestId": "85842548-131d-4b3c-b79d-acb6ec4470b3", "LogicalResourceId": "ASGCapacityProviderCreator", "ResourceType": "Custom::ASGCapacityProvider", "ResourceProperties": {"ServiceToken": "arn:aws:lambda:us-east-1:342241566140:function:CustomResource10-ASGCapacityProviderCreatorFunctio-1A0YGN2SCJAEY", "ECS_CLUSTER_NAME": "Issue-Reproduce-1", "ASG_NAME": "Issue-Reproduce-ASG-t2micro"}}
# context = {"log_stream_name": "MyTestLogStream"}
# from collections import namedtuple
# Test = namedtuple('context', 'log_stream_name')
# context = Test('MyTestLogStream')

# zip -r ../myDeploymentPackage.zip .


