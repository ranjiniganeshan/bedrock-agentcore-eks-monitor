# ─── Private ECR repository for AgentCore agent image ─────────────────────────

resource "aws_ecr_repository" "k8s_agent" {
  name                 = "k8s-agent"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = false
  }
}

resource "aws_ecr_lifecycle_policy" "k8s_agent" {
  repository = aws_ecr_repository.k8s_agent.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 5 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 5
        }
        action = { type = "expire" }
      }
    ]
  })
}
