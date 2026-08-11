locals {
  public_subnets = {
    for index, availability_zone in var.availability_zones :
    availability_zone => {
      cidr = var.public_subnet_cidrs[index]
      name = "${var.cluster_name}-public-${index + 1}"
    }
  }
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name = "${var.cluster_name}-vpc"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${var.cluster_name}-internet-gateway"
  }
}

resource "aws_subnet" "public" {
  for_each = local.public_subnets

  availability_zone       = each.key
  cidr_block              = each.value.cidr
  map_public_ip_on_launch = true
  vpc_id                  = aws_vpc.main.id

  tags = {
    Name = each.value.name
    Tier = "public"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${var.cluster_name}-public"
  }
}

resource "aws_route" "public_internet" {
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.main.id
  route_table_id         = aws_route_table.public.id
}

resource "aws_route_table_association" "public" {
  for_each = aws_subnet.public

  route_table_id = aws_route_table.public.id
  subnet_id      = each.value.id
}

resource "aws_security_group" "control_plane" {
  name        = "${var.cluster_name}-control-plane"
  description = "Restricted administration and worker traffic for the control plane"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "${var.cluster_name}-control-plane"
    Role = "control-plane"
  }
}

resource "aws_security_group" "worker" {
  name        = "${var.cluster_name}-workers"
  description = "Restricted administration and cluster-internal worker traffic"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "${var.cluster_name}-workers"
    Role = "worker"
  }
}

resource "aws_vpc_security_group_ingress_rule" "control_plane_ssh" {
  security_group_id = aws_security_group.control_plane.id
  cidr_ipv4         = var.administrator_cidr
  description       = "SSH from the trusted administrator CIDR"
  from_port         = 22
  ip_protocol       = "tcp"
  to_port           = 22
}

resource "aws_vpc_security_group_ingress_rule" "control_plane_api_admin" {
  security_group_id = aws_security_group.control_plane.id
  cidr_ipv4         = var.administrator_cidr
  description       = "Kubernetes API from the trusted administrator CIDR"
  from_port         = 6443
  ip_protocol       = "tcp"
  to_port           = 6443
}

resource "aws_vpc_security_group_ingress_rule" "control_plane_self" {
  security_group_id            = aws_security_group.control_plane.id
  description                  = "Control-plane component traffic"
  ip_protocol                  = "-1"
  referenced_security_group_id = aws_security_group.control_plane.id
}

resource "aws_vpc_security_group_ingress_rule" "control_plane_workers" {
  security_group_id            = aws_security_group.control_plane.id
  description                  = "Kubernetes node traffic from workers"
  ip_protocol                  = "-1"
  referenced_security_group_id = aws_security_group.worker.id
}

resource "aws_vpc_security_group_ingress_rule" "worker_ssh" {
  security_group_id = aws_security_group.worker.id
  cidr_ipv4         = var.administrator_cidr
  description       = "SSH from the trusted administrator CIDR"
  from_port         = 22
  ip_protocol       = "tcp"
  to_port           = 22
}

resource "aws_vpc_security_group_ingress_rule" "worker_control_plane" {
  security_group_id            = aws_security_group.worker.id
  description                  = "Kubernetes node traffic from the control plane"
  ip_protocol                  = "-1"
  referenced_security_group_id = aws_security_group.control_plane.id
}

resource "aws_vpc_security_group_ingress_rule" "worker_peers" {
  security_group_id            = aws_security_group.worker.id
  description                  = "Kubernetes and CNI traffic between workers"
  ip_protocol                  = "-1"
  referenced_security_group_id = aws_security_group.worker.id
}

resource "aws_vpc_security_group_egress_rule" "control_plane" {
  security_group_id = aws_security_group.control_plane.id
  cidr_ipv4         = "0.0.0.0/0"
  description       = "Control-plane package, registry, and AWS API access"
  ip_protocol       = "-1"
}

resource "aws_vpc_security_group_egress_rule" "worker" {
  security_group_id = aws_security_group.worker.id
  cidr_ipv4         = "0.0.0.0/0"
  description       = "Worker package, registry, and AWS API access"
  ip_protocol       = "-1"
}
