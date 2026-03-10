output "agentcore_runtime_role_arn" {
  description = "IAM role ARN for the Bedrock AgentCore Runtime"
  value       = aws_iam_role.agentcore_runtime_role.arn
}

output "agentcore_runtime_id" {
  description = "Bedrock AgentCore Runtime ID (written by deploy script)"
  value       = trimspace(data.local_file.agentcore_runtime_id.content)
  sensitive   = false
}

output "agentcore_artifacts_bucket" {
  description = "S3 bucket holding the AgentCore code zip"
  value       = aws_s3_bucket.agentcore_artifacts.id
}

output "webhook_server_irsa_role_arn" {
  description = "IRSA IAM role ARN for the webhook server pod"
  value       = aws_iam_role.webhook_server_irsa.arn
}

output "webhook_alertmanager_url" {
  description = "Alertmanager webhook URL (configure in alertmanager-secret.yaml)"
  value       = "http://alertmanager-webhook-server.alertmanager-agent.svc.cluster.local/api/investigate"
}
