# api/proxy_endpoints.py

@app.get("/api/proxy/chains")
async def get_proxy_chains():
    """Get all proxy chains and their health"""
    chains = []
    for name, chain in proxy_manager.chains.items():
        chains.append({
            'name': name,
            'total_nodes': len(chain.nodes),
            'healthy_nodes': len([n for n in chain.nodes if n.is_healthy]),
            'avg_response_time': sum(n.avg_response_time for n in chain.nodes) / len(chain.nodes),
            'strategy': chain.strategy,
            'nodes': [
                {
                    'host': n.host,
                    'country': n.country,
                    'type': n.type,
                    'is_healthy': n.is_healthy,
                    'success_rate': n.success_count / max(n.success_count + n.fail_count, 1),
                    'banned_on': n.is_banned_on
                }
                for n in chain.nodes
            ]
        })
    return chains

@app.post("/api/proxy/chains/{name}/rotate")
async def force_proxy_rotation(name: str):
    """Force rotation of all IPs in chain"""
    chain = proxy_manager.chains.get(name)
    if not chain:
        raise HTTPException(404, "Chain not found")
    
    # Reset all cooldowns
    for node in chain.nodes:
        node.last_used = None
    
    return {"status": "rotated", "nodes_reset": len(chain.nodes)}

@app.get("/api/proxy/stats")
async def get_proxy_statistics(days: int = 7):
    """Get proxy usage statistics"""
    return {
        'requests_by_chain': {},
        'success_rates': {},
        'bans_by_platform': {},
        'avg_response_times': {}
    }