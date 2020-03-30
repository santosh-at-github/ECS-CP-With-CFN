# ECS-CP-With-CFN
Deploy ECS ASG-Capacity-Provider along with an ECS Service using CloudFormation Custom Resource

To deploy this stack, clone this repo in a directory and upload all it's file to a S3 bucket in the same region where you would like to deploy the CloudFormation Stack. Then deploy a CloudFormation stack using the "Master-Stack.yaml" template by provding all the input parameters (default value of parameters are just for example). This will create mutiple nested stacks (8 to be precise) and deploy the solution in your account.

At the time of writing this solution, AWS CloudFormation do not support creating ECS Capacity Provider. This stack is an example implemetation to deploy ECS Capacity Provider using Lambda Function CloudFormation Custom Resource.

At this time, Lambda also ships with older version of boto3 which do not have API implemented to create ECS Capacity Provider. I have included the latest version of boto3 (at the time of writing this) in the lambda zip package to make it work.

