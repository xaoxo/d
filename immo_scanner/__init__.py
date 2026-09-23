__version__ = "0.2.0"

try:  # certificats vérifiés par le système d'exploitation, comme le navigateur
    import truststore

    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001 - facultatif : sinon, vérification standard de Python
    pass
