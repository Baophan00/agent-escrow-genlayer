import { readFileSync } from "fs";
import path from "path";
import {
  TransactionStatus
} from "genlayer-js/types";
import { localnet } from "genlayer-js/chains";
async function main(client) {
  const filePath = path.resolve(process.cwd(), "contracts/agent_escrow.py");
  try {
    const contractCode = new Uint8Array(readFileSync(filePath));
    await client.initializeConsensusSmartContract();
    const deployTransaction = await client.deployContract({
      code: contractCode,
      args: []
    });
    const receipt = await client.waitForTransactionReceipt({
      hash: deployTransaction,
      status: TransactionStatus.ACCEPTED,
      retries: 200
    });
    if (receipt.status !== 5 && receipt.status !== 6 && receipt.statusName !== "ACCEPTED" && receipt.statusName !== "FINALIZED") {
      throw new Error(`Deployment failed. Receipt: ${JSON.stringify(receipt)}`);
    }
    const deployedContractAddress = client.chain.id === localnet.id ? receipt.data.contract_address : receipt.txDataDecoded?.contractAddress;
    console.log(`Contract deployed at address: ${deployedContractAddress}`);
  } catch (error) {
    throw new Error(`Error during deployment:, ${error}`);
  }
}
export {
  main as default
};
