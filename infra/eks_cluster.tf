# EKS cluster already exists — use data sources to reference it
data "aws_eks_cluster" "example" {
  name = "demo-cluster"
}

data "aws_eks_cluster_auth" "example" {
  name = "demo-cluster"
}

# Output the EKS cluster endpoint
output "cluster_endpoint" {
  value = data.aws_eks_cluster.example.endpoint
}

# Output the cluster name
output "cluster_name" {
  value = data.aws_eks_cluster.example.name
}
