FROM odoo@sha256:4872f23288454b724fd2d26c176a418276c2b3552e9aa752f9396b59d864b3a0

USER root

COPY docker/odoo-requirements.txt /tmp/odoo-requirements.txt

RUN python3 -m pip install --no-cache-dir --break-system-packages \
        --ignore-installed --require-hashes \
        --requirement /tmp/odoo-requirements.txt \
    && rm /tmp/odoo-requirements.txt

COPY --chown=odoo:odoo odoo/addons/stockai_procurement /mnt/extra-addons/stockai_procurement
COPY --chown=odoo:odoo odoo/bootstrap/bootstrap.py /opt/stockai/bootstrap.py
COPY --chown=odoo:odoo odoo/bootstrap/sinks.py /opt/stockai/sinks.py
COPY --chown=odoo:odoo scripts/odoo/seed.py /opt/stockai/seed.py
COPY --chown=odoo:odoo scripts/odoo/verify_seed.py /opt/stockai/verify_seed.py

USER odoo
