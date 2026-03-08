"""Auto-redemption of resolved Polymarket positions."""

from decimal import Decimal
from typing import Any

import aiohttp

from orangebot.config import get_settings
from orangebot.utils.logging import get_logger

log = get_logger(__name__)

# Polymarket contracts on Polygon
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"


async def get_redeemable_positions(wallet_address: str) -> list[dict]:
    """Fetch positions that are ready to redeem (market resolved)."""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                f"https://data-api.polymarket.com/positions?user={wallet_address}"
            ) as resp:
                if resp.status != 200:
                    return []
                positions = await resp.json()

            return [
                p for p in positions
                if p.get("redeemable") and float(p.get("size", 0)) > 0
            ]
        except Exception as e:
            log.error("Failed to fetch redeemable positions", error=str(e))
            return []


async def redeem_position(
    position: dict,
    wallet_address: str,
    private_key: str,
    rpc_url: str,
    chain_id: int = 137,
) -> dict[str, Any]:
    """Redeem a single resolved position back to USDC."""
    from web3 import Web3

    try:
        w3 = Web3(Web3.HTTPProvider(rpc_url))

        condition_id = position.get("conditionId")
        size = int(float(position.get("size", 0)))
        neg_risk = position.get("negRisk", False)

        if not condition_id or size == 0:
            return {"success": False, "reason": "invalid position data"}

        # Minimal ABI for CTF redeem
        REDEEM_ABI = [
            {
                "inputs": [
                    {"name": "collateralToken", "type": "address"},
                    {"name": "parentCollectionId", "type": "bytes32"},
                    {"name": "conditionId", "type": "bytes32"},
                    {"name": "indexSets", "type": "uint256[]"},
                ],
                "name": "redeemPositions",
                "outputs": [],
                "stateMutability": "nonpayable",
                "type": "function",
            }
        ]

        USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
        contract_address = NEG_RISK_CTF_EXCHANGE if neg_risk else CTF_EXCHANGE

        contract = w3.eth.contract(
            address=Web3.to_checksum_address(CTF_ADDRESS),
            abi=REDEEM_ABI,
        )

        wallet = Web3.to_checksum_address(wallet_address)
        nonce = w3.eth.get_transaction_count(wallet)
        gas_price = w3.eth.gas_price

        # Index sets: [1] = YES won, [2] = NO won
        index_sets = [1, 2]

        tx = contract.functions.redeemPositions(
            Web3.to_checksum_address(USDC_ADDRESS),
            b"\x00" * 32,  # parentCollectionId = zero
            bytes.fromhex(condition_id.replace("0x", "")),
            index_sets,
        ).build_transaction({
            "from": wallet,
            "nonce": nonce,
            "gas": 200000,
            "gasPrice": int(gas_price * 1.1),
            "chainId": chain_id,
        })

        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

        success = receipt["status"] == 1
        value = float(position.get("currentValue", 0))

        return {
            "success": success,
            "tx_hash": tx_hash.hex(),
            "value": value,
            "market": position.get("title", "Unknown"),
        }

    except Exception as e:
        log.error("Redemption failed", error=str(e))
        return {"success": False, "error": str(e)}


async def check_and_redeem() -> dict[str, Any]:
    """Check for and redeem all resolved positions."""
    settings = get_settings()

    if not settings.wallet_address or not settings.private_key:
        return {"skipped": True, "reason": "wallet not configured"}

    positions = await get_redeemable_positions(settings.wallet_address)

    if not positions:
        return {"redeemed": 0, "total_value": 0.0}

    private_key = settings.private_key.get_secret_value()
    redeemed = 0
    total_value = 0.0

    for position in positions:
        result = await redeem_position(
            position=position,
            wallet_address=settings.wallet_address,
            private_key=private_key,
            rpc_url=settings.polygon_rpc_url,
            chain_id=settings.chain_id,
        )
        if result.get("success"):
            redeemed += 1
            total_value += result.get("value", 0.0)
            log.info(
                "Position redeemed",
                market=result.get("market", "")[:40],
                value=f"${result.get('value', 0):.2f}",
                tx=result.get("tx_hash", "")[:16],
            )

    return {"redeemed": redeemed, "total_value": total_value}


async def redeem_all_positions() -> dict[str, Any]:
    """Public interface for CLI redeem command."""
    return await check_and_redeem()
