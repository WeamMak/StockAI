# Dev reconciliation recovery

Use this once to replace the three never-mounted dev PersistentVolumes that
were created with placeholder EBS handles. Run AWS checks locally, then run the
Kubernetes commands in the control-plane SSM session.

The PersistentVolume reclaim policy is `Retain`. This procedure removes only
Kubernetes objects; it does not remove EBS volumes.

## Stop conditions

Stop if a target EBS volume is absent, attached, outside `us-east-1a`, or
different from the ID stored in Git. Stop if a target has a
`VolumeAttachment`. Stop on a stuck deletion; do not remove finalizers. Stop if
the Terraform apply includes replacement, deletion, or unrelated networking.
Stop if External Secrets still times out to the Kubernetes API after the
security-group rule is live.

## 1. Check the EBS volumes locally

```bash
aws ec2 describe-volumes --region us-east-1 --volume-ids \
  vol-051d6c42ca98f0b15 \
  vol-0491b34550d11b018 \
  vol-01ab986773724a6b1 \
  --query 'Volumes[].{Id:VolumeId,State:State,AZ:AvailabilityZone,Attachments:Attachments}'
aws ssm start-session --region us-east-1 --target i-02ca9a315122c8c77
```

Continue only when all three volumes are `available`, unattached, and in
`us-east-1a`.

## 2. Check Kubernetes in the SSM session

```bash
sudo -i
export KUBECONFIG=/etc/kubernetes/admin.conf
kubectl get volumeattachments
kubectl get pv stockai-dev-odoo-filestore \
  stockai-dev-postgresql-data stockai-dev-prometheus-data \
  -o custom-columns='NAME:.metadata.name,HANDLE:.spec.csi.volumeHandle,RECLAIM:.spec.persistentVolumeReclaimPolicy,STATUS:.status.phase'
```

Continue only when none of the three targets has a `VolumeAttachment` and each
PV shows `Retain`.

## 3. Recreate only the invalid storage objects

First pause automated reconciliation:

```bash
kubectl -n argocd patch application stockai-dev --type=json \
  -p='[{"op":"remove","path":"/spec/syncPolicy/automated"}]'
kubectl -n dev delete deployment \
  stockai-odoo stockai-postgresql stockai-prometheus
kubectl -n dev delete job stockai-odoo-bootstrap --ignore-not-found
kubectl -n dev wait --for=delete \
  deployment/stockai-odoo \
  deployment/stockai-postgresql \
  deployment/stockai-prometheus \
  --timeout=120s
kubectl -n dev delete pvc \
  odoo-filestore postgresql-data prometheus-data
kubectl -n dev wait --for=delete \
  pvc/odoo-filestore pvc/postgresql-data pvc/prometheus-data \
  --timeout=120s
kubectl delete pv \
  stockai-dev-odoo-filestore \
  stockai-dev-postgresql-data \
  stockai-dev-prometheus-data
kubectl wait --for=delete \
  pv/stockai-dev-odoo-filestore \
  pv/stockai-dev-postgresql-data \
  pv/stockai-dev-prometheus-data \
  --timeout=120s
```

Then restore automated reconciliation:

```bash
kubectl -n argocd patch application stockai-dev --type=merge \
  -p='{"spec":{"syncPolicy":{"automated":{"prune":true,"selfHeal":true}}}}'
```

Argo CD, not this procedure, recreates the desired storage and workloads.

## 4. Verify convergence

```bash
kubectl get pv \
  stockai-dev-odoo-filestore \
  stockai-dev-postgresql-data \
  stockai-dev-prometheus-data \
  -o custom-columns='NAME:.metadata.name,HANDLE:.spec.csi.volumeHandle,STATUS:.status.phase'
kubectl -n dev get pvc odoo-filestore postgresql-data prometheus-data
kubectl -n dev get secretstore,externalsecret
kubectl -n dev get pods
kubectl -n argocd get application stockai-dev
```

The PV handles must respectively be:

- `vol-051d6c42ca98f0b15`
- `vol-0491b34550d11b018`
- `vol-01ab986773724a6b1`

All three PVs and PVCs must be bound, all ExternalSecrets must be ready, the
workloads must be healthy, and Argo CD must report `Synced` and `Healthy`.
