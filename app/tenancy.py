from __future__ import annotations
import os, yaml
from typing import Dict, Optional, List
from fastapi import Request
from .pricing import PricingEngine

class TenantManager:
    def __init__(self, tenants_dir: str):
        self.tenants_dir = tenants_dir
        self._cache: Dict[str, PricingEngine] = {}
        self._did_map = self._load_did_map()

    def _load_did_map(self) -> Dict[str,str]:
        mapping: Dict[str,str] = {}
        if not os.path.isdir(self.tenants_dir): return mapping
        for f in os.listdir(self.tenants_dir):
            if not f.endswith('.yaml'): continue
            path = os.path.join(self.tenants_dir, f)
            try:
                cfg = yaml.safe_load(open(path,'r',encoding='utf-8'))
                tname = cfg.get('business',{}).get('slug') or f.split('.')[0]
                for did in cfg.get('telephony',{}).get('did', []):
                    mapping[str(did)] = tname
            except Exception: pass
        return mapping

    def list_tenants(self) -> List[str]:
        names = []
        if os.path.isdir(self.tenants_dir):
            for f in os.listdir(self.tenants_dir):
                if f.endswith('.yaml'): names.append(f.split('.')[0])
        return sorted(names)

    def path_for(self, tenant: str) -> str:
        candidate = os.path.join(self.tenants_dir, f"{tenant}.yaml")
        if os.path.isfile(candidate): return candidate
        raise FileNotFoundError(f"Unknown tenant '{tenant}'. Add tenants/{tenant}.yaml")

    def get_engine(self, tenant: str) -> PricingEngine:
        key = tenant
        if key not in self._cache:
            path = self.path_for(tenant)
            self._cache[key] = PricingEngine(path)
        return self._cache[key]

def resolve_tenant_name(request: Request, header_name: str = 'X-Tenant', use_did: bool = True) -> Optional[str]:
    t = request.headers.get(header_name)
    if t: return t
    if use_did:
        did = request.headers.get('X-Caller-DID') or request.headers.get('X-Twilio-Called')
        if did: return did.strip().replace(' ','')
    return None
