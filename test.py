import json
from web3 import Web3
from web3.contract import Contract
from eth_utils import to_checksum_address
from typing import Dict, Any, Optional

class UniswapV3Decoder:
    POOL_ABI = [
        {
            "constant": True,
            "inputs": [],
            "name": "token0",
            "outputs": [{"internalType": "address", "name": "", "type": "address"}],
            "type": "function"
        },
        {
            "constant": True,
            "inputs": [],
            "name": "token1",
            "outputs": [{"internalType": "address", "name": "", "type": "address"}],
            "type": "function"
        },
        {
            "anonymous": False,
            "inputs": [
                {"indexed": True, "name": "sender", "type": "address"},
                {"indexed": True, "name": "recipient", "type": "address"},
                {"indexed": False, "name": "amount0", "type": "int256"},
                {"indexed": False, "name": "amount1", "type": "int256"},
                {"indexed": False, "name": "sqrtPriceX96", "type": "uint160"},
                {"indexed": False, "name": "liquidity", "type": "uint128"},
                {"indexed": False, "name": "tick", "type": "int24"}
            ],
            "name": "Swap",
            "type": "event"
        }
    ]
    
    # ERC20 ABI (partial - just for decimals)
    ERC20_ABI = [
        {
            "constant": True,
            "inputs": [],
            "name": "decimals",
            "outputs": [{"name": "", "type": "uint8"}],
            "type": "function"
        },
        {
            "constant": True,
            "inputs": [],
            "name": "symbol",
            "outputs": [{"name": "", "type": "string"}],
            "type": "function"
        }
    ]
    
    # Uniswap V3 Router ABI (partial)
    ROUTER_ABI = [
        {
            "inputs": [
                {
                    "components": [
                        {"internalType": "bytes", "name": "path", "type": "bytes"},
                        {"internalType": "address", "name": "recipient", "type": "address"},
                        {"internalType": "uint256", "name": "deadline", "type": "uint256"},
                        {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                        {"internalType": "uint256", "name": "amountOutMinimum", "type": "uint256"}
                    ],
                    "internalType": "struct ISwapRouter.ExactInputParams",
                    "name": "params",
                    "type": "tuple"
                }
            ],
            "name": "exactInput",
            "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
            "stateMutability": "payable",
            "type": "function"
        }
    ]
    
    # Known Uniswap V3 addresses
    UNISWAP_V3_ROUTER = "0xE592427A0AEce92De3Edee1F18E0157C05861564"
    UNISWAP_V3_ROUTER_2 = "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45"
    
    # 替换 __init__ 方法中的默认 URL
    def __init__(self, rpc_url: str = None):
        """Initialize decoder with RPC URL"""
        
        # 如果没有提供 URL，使用公共节点
        if rpc_url is None:
            # 选择其中一个公共节点
            public_nodes = [
                "https://ethereum.publicnode.com",  # Public Node
            ]
            
            # 尝试每个节点直到成功
            for node_url in public_nodes:
                try:
                    self.w3 = Web3(Web3.HTTPProvider(node_url))
                    if self.w3.is_connected():
                        print(f"Connected to: {node_url}")
                        break
                except:
                    continue
            else:
                raise ConnectionError("Failed to connect to any public node")
        else:
            # 使用用户提供的 URL
            self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        
        # 最终检查
        if not self.w3.is_connected():
            raise ConnectionError("Failed to connect to Ethereum node")
    
    def decode_swap(self, tx_hash: str) -> Dict[str, Any]:
        """Main function to decode a Uniswap V3 swap transaction"""
        
        # Get transaction and receipt
        tx = self.w3.eth.get_transaction(tx_hash)
        receipt = self.w3.eth.get_transaction_receipt(tx_hash)
        
        # Get sender
        sender = tx['from']
        
        # Find Swap events in logs
        swap_events = []
        for log in receipt['logs']:
            try:
                # Check if this is a Swap event from a Uniswap V3 pool
                pool_contract = self.w3.eth.contract(
                    address=log['address'],
                    abi=self.POOL_ABI
                )
                
                # Try to decode as Swap event
                event_signature = self.w3.keccak(text="Swap(address,address,int256,int256,uint160,uint128,int24)").hex()
                if log['topics'][0].hex() == event_signature:
                    decoded_event = pool_contract.events.Swap().process_log(log)
                    swap_events.append({
                        'pool': log['address'],
                        'event': decoded_event
                    })
            except:
                continue
        
        if not swap_events:
            raise ValueError("No Uniswap V3 swap events found in transaction")
        
        # For simplicity, take the first swap event (most transactions have only one)
        swap_event = swap_events[0]['event']
        pool_address = swap_events[0]['pool']
        
        # Get pool contract to fetch tokens
        pool_contract = self.w3.eth.contract(
            address=pool_address,
            abi=self.POOL_ABI
        )
        
        token0 = pool_contract.functions.token0().call()
        token1 = pool_contract.functions.token1().call()
        
        # Determine which token is input and which is output
        amount0 = swap_event['args']['amount0']
        amount1 = swap_event['args']['amount1']
        
        # Positive amount = pool received, Negative amount = pool sent
        if amount0 < 0 and amount1 > 0:
            token_in = token0
            token_out = token1
            amount_in_abs = abs(amount0)
            amount_out_abs = amount1
        elif amount1 < 0 and amount0 > 0:
            token_in = token1
            token_out = token0
            amount_in_abs = abs(amount1)
            amount_out_abs = amount0
        else:
            raise ValueError("Invalid swap amounts - could not determine direction")
        
        # Get token decimals
        token_in_decimals = self._get_token_decimals(token_in)
        token_out_decimals = self._get_token_decimals(token_out)
        
        # Calculate human-readable amounts
        amount_in_human = amount_in_abs / (10 ** token_in_decimals)
        amount_out_human = amount_out_abs / (10 ** token_out_decimals)
        
        # Try to get the recipient from the swap event or transaction
        recipient = swap_event['args']['recipient']
        
        # If recipient is a router, try to decode the transaction input to find final recipient
        if recipient.lower() in [self.UNISWAP_V3_ROUTER.lower(), self.UNISWAP_V3_ROUTER_2.lower()]:
            try:
                # Try to decode router call
                router_contract = self.w3.eth.contract(
                    address=to_checksum_address(self.UNISWAP_V3_ROUTER),
                    abi=self.ROUTER_ABI
                )
                
                # Decode the transaction input
                func_obj, func_params = router_contract.decode_function_input(tx['input'])
                
                if func_obj.fn_name == "exactInput":
                    # The recipient is in the params
                    recipient = func_params['params']['recipient']
            except:
                # If decoding fails, use the swap event recipient
                pass
        
        # Get token symbols (optional, for better readability)
        try:
            token_in_symbol = self._get_token_symbol(token_in)
            token_out_symbol = self._get_token_symbol(token_out)
            token_display = f"{token_in_symbol} ({token_in}) / {token_out_symbol} ({token_out})"
        except:
            token_display = f"{token_in} / {token_out}"
        
        result = {
            "transaction_hash": tx_hash,
            "sender": sender,
            "recipient": recipient,
            "token_in": token_in,
            "token_out": token_out,
            "token_display": token_display,
            "amount_in": str(amount_in_human),
            "amount_out": str(amount_out_human),
        }
        
        return result
    
    def _get_token_decimals(self, token_address: str) -> int:
        """Get token decimals"""
        try:
            token_contract = self.w3.eth.contract(
                address=token_address,
                abi=self.ERC20_ABI
            )
            return token_contract.functions.decimals().call()
        except:
            # Default to 18 for ETH and most tokens if call fails
            return 18
    
    def _get_token_symbol(self, token_address: str) -> str:
        """Get token symbol"""
        try:
            token_contract = self.w3.eth.contract(
                address=token_address,
                abi=self.ERC20_ABI
            )
            return token_contract.functions.symbol().call()
        except:
            return "UNKNOWN"

def main():
    """Example usage with test transactions"""
    
    # Initialize decoder
    decoder = UniswapV3Decoder()
    
    # Test transaction hashes
    test_transactions = [
        "0x7fdee03ffb227454946852b815b6b86d38e77e6190985c1816b41a8a7b790ea0",
        
        "0x0d903486074e99d08925bc4d342f8da7f37f71a417784890f5f7f18373cc1701",
        "0xb7af02609c96df273f49dfb0d3feba5ab31ff80045e82804b7bf04b7d4ded2cb"
    ]
    
    print("Uniswap V3 Transaction Decoder")
    print("=" * 60)
    
    for tx_hash in test_transactions:
        try:
            print(f"\nDecoding transaction: {tx_hash}")
            result = decoder.decode_swap(tx_hash)
            print(json.dumps(result, indent=2, default=str))
            print("-" * 60)
        except Exception as e:
            print(f"Error decoding {tx_hash}: {str(e)}")
            print("-" * 60)

if __name__ == "__main__":
    main()