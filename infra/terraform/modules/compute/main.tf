locals {
  workers = {
    for environment in ["dev", "prod"] : environment => {
      availability_zone     = var.worker_availability_zones[environment]
      capacity              = lookup(var.worker_capacity_overrides, environment, var.worker_capacity)
      instance_profile_name = var.worker_instance_profile_names[environment]
      subnet_id             = var.worker_subnet_ids[environment]
    }
  }
}

resource "aws_instance" "control_plane" {
  ami                         = var.ami_id
  associate_public_ip_address = true
  iam_instance_profile        = var.control_plane_instance_profile_name
  instance_type               = "t3.medium"
  subnet_id                   = var.control_plane_subnet_id
  vpc_security_group_ids      = [var.control_plane_security_group_id]

  metadata_options {
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 1
    http_tokens                 = "required"
    instance_metadata_tags      = "disabled"
  }

  root_block_device {
    delete_on_termination = true
    encrypted             = true
    volume_size           = 30
    volume_type           = "gp3"
  }

  tags = {
    Environment = "shared"
    Name        = "${var.cluster_name}-control-plane"
    Owner       = var.owner_name
    Role        = "control-plane"
  }

  volume_tags = {
    Environment = "shared"
    Name        = "${var.cluster_name}-control-plane-root"
    Owner       = var.owner_name
    Role        = "control-plane"
  }
}

resource "aws_launch_template" "worker" {
  for_each = local.workers

  image_id               = var.ami_id
  instance_type          = "t3.medium"
  name_prefix            = "${var.cluster_name}-${each.key}-worker-"
  update_default_version = true
  user_data = base64encode(templatefile("${path.module}/worker-user-data.sh.tftpl", {
    cluster_name = var.cluster_name
    environment  = each.key
  }))

  block_device_mappings {
    device_name = "/dev/sda1"

    ebs {
      delete_on_termination = true
      encrypted             = true
      volume_size           = 30
      volume_type           = "gp3"
    }
  }

  iam_instance_profile {
    name = each.value.instance_profile_name
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 1
    http_tokens                 = "required"
    instance_metadata_tags      = "disabled"
  }

  network_interfaces {
    associate_public_ip_address = true
    delete_on_termination       = true
    device_index                = 0
    security_groups             = [var.worker_security_group_id]
  }

  tag_specifications {
    resource_type = "instance"

    tags = {
      Environment = each.key
      Name        = "${var.cluster_name}-${each.key}-worker"
      Owner       = var.owner_name
      Role        = "worker"
    }
  }

  tag_specifications {
    resource_type = "volume"

    tags = {
      Environment = each.key
      Name        = "${var.cluster_name}-${each.key}-worker-root"
      Owner       = var.owner_name
      Role        = "worker"
    }
  }
}

resource "aws_autoscaling_group" "worker" {
  for_each = local.workers

  name                      = "${var.cluster_name}-${each.key}-workers"
  desired_capacity          = each.value.capacity.desired
  health_check_grace_period = 300
  health_check_type         = "EC2"
  max_size                  = each.value.capacity.max
  min_size                  = each.value.capacity.min
  vpc_zone_identifier       = [each.value.subnet_id]

  launch_template {
    id      = aws_launch_template.worker[each.key].id
    version = aws_launch_template.worker[each.key].latest_version
  }

  # EC2 InService only reports instance health. Kubernetes Ready and workload
  # continuity remain separate acceptance checks. This configuration starts a
  # replacement first where the ASG's configured maximum leaves headroom.
  instance_refresh {
    strategy = "Rolling"

    preferences {
      auto_rollback          = true
      instance_warmup        = 300
      max_healthy_percentage = 200
      min_healthy_percentage = 100
      skip_matching          = true
    }
  }

  tag {
    key                 = "Environment"
    propagate_at_launch = true
    value               = each.key
  }

  tag {
    key                 = "Owner"
    propagate_at_launch = true
    value               = var.owner_name
  }
}
