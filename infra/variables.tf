variable "aws_account_id" {
  description = "AWS account ID"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "agentcore_container_image" {
  description = "Docker Hub image for the AgentCore Runtime agent (pre-baked deps = fast cold start)"
  type        = string
  default     = ""
}

variable "bedrock_model_id" {
  description = "Bedrock model ID used by the AgentCore Runtime agent"
  type        = string
  default     = "anthropic.claude-3-haiku-20240307-v1:0"
}

variable "jira_base_url" {
  description = "JIRA base URL for escalation (optional)"
  type        = string
  default     = ""
}

variable "jira_project_key" {
  description = "JIRA project key (optional)"
  type        = string
  default     = ""
}

variable "jira_email" {
  description = "JIRA email for API authentication (optional)"
  type        = string
  default     = ""
}

variable "jira_api_token" {
  description = "JIRA API token (optional)"
  type        = string
  sensitive   = true
  default     = ""
}

variable "slack_webhook_url" {
  description = "Slack webhook URL for escalation alerts (optional)"
  type        = string
  sensitive   = true
  default     = ""
}
