from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from governor.activities import Activities
from governor.config import Config
from governor.workflow import CardinalityWorkflow


async def run_worker():
    config = Config.from_env()
    activities = Activities(config)
    client = await Client.connect(config.temporal_address, namespace=config.temporal_namespace)
    with ThreadPoolExecutor(max_workers=8) as executor:
        worker = Worker(
            client,
            task_queue=config.task_queue,
            workflows=[CardinalityWorkflow],
            activities=activities.registered(),
            activity_executor=executor,
        )
        await worker.run()
