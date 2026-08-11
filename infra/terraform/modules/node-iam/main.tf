locals {
  nodes = {
    control_plane = {
      environment = "shared"
      role        = "control-plane"
    }
    dev_worker = {
      environment = "dev"
      role        = "worker"
    }
    prod_worker = {
      environment = "prod"
      role        = "worker"
    }
  }
}

data "aws_iam_policy_document" "ec2_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node" {
  for_each = local.nodes

  name               = "${var.cluster_name}-${replace(each.key, "_", "-")}"
  description        = "${title(replace(each.value.role, "-", " "))} instance role for ${each.value.environment}"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json

  tags = {
    Environment = each.value.environment
    Role        = each.value.role
  }
}

# T16 grants only the channels required for managed-instance administration.
# Environment application permissions and the exact join-parameter policy are
# added by their later approved tasks.
resource "aws_iam_role_policy_attachment" "ssm_managed_instance" {
  for_each = aws_iam_role.node

  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
  role       = each.value.name
}

resource "aws_iam_instance_profile" "node" {
  for_each = aws_iam_role.node

  name = each.value.name
  role = each.value.name
}
