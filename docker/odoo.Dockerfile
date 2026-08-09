FROM odoo@sha256:4872f23288454b724fd2d26c176a418276c2b3552e9aa752f9396b59d864b3a0

USER root

RUN python3 -m pip install --no-cache-dir --break-system-packages boto3==1.43.62

COPY --chown=odoo:odoo odoo/addons/stockai_procurement /mnt/extra-addons/stockai_procurement
COPY --chown=odoo:odoo odoo/bootstrap/bootstrap.py /opt/stockai/bootstrap.py

USER odoo
