import asyncio
import functools
import math
import os
import tempfile
import traceback
import uuid
from typing import Tuple, List, Any

import aiohttp
import discord

# An enum of two modes, TOP_P or TEMPERATURE
import requests
from PIL import Image
from discord import File


class Mode:
    TOP_P = "top_p"
    TEMPERATURE = "temperature"


class Models:
    DAVINCI = "text-davinci-003"
    CURIE = "text-curie-001"
    EMBEDDINGS = "text-embedding-ada-002"
    EDIT = "text-davinci-edit-001"
    CODE_EDIT = "code-davinci-edit-001"


class ImageSize:
    LARGE = "1024x1024"
    MEDIUM = "512x512"
    SMALL = "256x256"


class Model:
    def __init__(self, usage_service):
        self._mode = Mode.TEMPERATURE
        self._temp = 0.6  # Higher value means more random, lower value means more likely to be a coherent sentence
        self._top_p = 0.9  # 1 is equivalent to greedy sampling, 0.1 means that the model will only consider the top 10% of the probability distribution
        self._max_tokens = 4000  # The maximum number of tokens the model can generate
        self._presence_penalty = (
            0  # Penalize new tokens based on whether they appear in the text so far
        )
        self._frequency_penalty = 0  # Penalize new tokens based on their existing frequency in the text so far. (Higher frequency = lower probability of being chosen.)
        self._best_of = 1  # Number of responses to compare the loglikelihoods of
        self._prompt_min_length = 8
        self._max_conversation_length = 100
        self._model = Models.DAVINCI
        self._low_usage_mode = False
        self.usage_service = usage_service
        self.DAVINCI_ROLES = ["admin", "Admin", "GPT", "gpt"]
        self._image_size = ImageSize.MEDIUM
        self._num_images = 2
        self._summarize_conversations = True
        self._summarize_threshold = 2500
        self.model_max_tokens = 4024
        self._welcome_message_enabled = True
        self._num_static_conversation_items = 8
        self._num_conversation_lookback = 6

        try:
            self.IMAGE_SAVE_PATH = os.environ["IMAGE_SAVE_PATH"]
            self.custom_image_path = True
        except:
            self.IMAGE_SAVE_PATH = "dalleimages"
            # Try to make this folder called images/ in the local directory if it doesnt exist
            if not os.path.exists(self.IMAGE_SAVE_PATH):
                os.makedirs(self.IMAGE_SAVE_PATH)
            self.custom_image_path = False

        self._hidden_attributes = [
            "usage_service",
            "DAVINCI_ROLES",
            "custom_image_path",
            "custom_web_root",
            "_hidden_attributes",
            "model_max_tokens",
            "openai_key",
        ]

        self.openai_key = os.getenv("OPENAI_TOKEN")

    # Use the @property and @setter decorators for all the self fields to provide value checking

    @property
    def num_static_conversation_items(self):
        return self._num_static_conversation_items

    @num_static_conversation_items.setter
    def num_static_conversation_items(self, value):
        value = int(value)
        if value < 3:
            raise ValueError("num_static_conversation_items must be >= 3")
        if value > 20:
            raise ValueError(
                "num_static_conversation_items must be <= 20, this is to ensure reliability and reduce token wastage!"
            )
        self._num_static_conversation_items = value

    @property
    def num_conversation_lookback(self):
        return self._num_conversation_lookback

    @num_conversation_lookback.setter
    def num_conversation_lookback(self, value):
        value = int(value)
        if value < 3:
            raise ValueError("num_conversation_lookback must be >= 3")
        if value > 15:
            raise ValueError(
                "num_conversation_lookback must be <= 15, this is to ensure reliability and reduce token wastage!"
            )
        self._num_conversation_lookback = value

    @property
    def welcome_message_enabled(self):
        return self._welcome_message_enabled

    @welcome_message_enabled.setter
    def welcome_message_enabled(self, value):
        if value.lower() == "true":
            self._welcome_message_enabled = True
        elif value.lower() == "false":
            self._welcome_message_enabled = False
        else:
            raise ValueError("Value must be either true or false!")

    @property
    def summarize_threshold(self):
        return self._summarize_threshold

    @summarize_threshold.setter
    def summarize_threshold(self, value):
        value = int(value)
        if value < 800 or value > 4000:
            raise ValueError(
                "Summarize threshold cannot be greater than 4000 or less than 800!"
            )
        self._summarize_threshold = value

    @property
    def summarize_conversations(self):
        return self._summarize_conversations

    @summarize_conversations.setter
    def summarize_conversations(self, value):
        # convert value string into boolean
        if value.lower() == "true":
            value = True
        elif value.lower() == "false":
            value = False
        else:
            raise ValueError("Value must be either true or false!")
        self._summarize_conversations = value

    @property
    def image_size(self):
        return self._image_size

    @image_size.setter
    def image_size(self, value):
        if value in ImageSize.__dict__.values():
            self._image_size = value
        else:
            raise ValueError(
                "Image size must be one of the following: SMALL(256x256), MEDIUM(512x512), LARGE(1024x1024)"
            )

    @property
    def num_images(self):
        return self._num_images

    @num_images.setter
    def num_images(self, value):
        value = int(value)
        if value > 4 or value <= 0:
            raise ValueError("num_images must be less than 4 and at least 1.")
        self._num_images = value

    @property
    def low_usage_mode(self):
        return self._low_usage_mode

    @low_usage_mode.setter
    def low_usage_mode(self, value):
        # convert value string into boolean
        if value.lower() == "true":
            value = True
        elif value.lower() == "false":
            value = False
        else:
            raise ValueError("Value must be either true or false!")

        if value:
            self._model = Models.CURIE
            self.max_tokens = 1900
            self.model_max_tokens = 1000
        else:
            self._model = Models.DAVINCI
            self.max_tokens = 4000
            self.model_max_tokens = 4024

    @property
    def model(self):
        return self._model

    @model.setter
    def model(self, model):
        if model not in [Models.DAVINCI, Models.CURIE]:
            raise ValueError(
                "Invalid model, must be text-davinci-003 or text-curie-001"
            )
        self._model = model

    @property
    def max_conversation_length(self):
        return self._max_conversation_length

    @max_conversation_length.setter
    def max_conversation_length(self, value):
        value = int(value)
        if value < 1:
            raise ValueError("Max conversation length must be greater than 1")
        if value > 500:
            raise ValueError(
                "Max conversation length must be less than 500, this will start using credits quick."
            )
        self._max_conversation_length = value

    @property
    def mode(self):
        return self._mode

    @mode.setter
    def mode(self, value):
        if value not in [Mode.TOP_P, Mode.TEMPERATURE]:
            raise ValueError("mode must be either 'top_p' or 'temperature'")
        if value == Mode.TOP_P:
            self._top_p = 0.1
            self._temp = 0.7
        elif value == Mode.TEMPERATURE:
            self._top_p = 0.9
            self._temp = 0.6

        self._mode = value

    @property
    def temp(self):
        return self._temp

    @temp.setter
    def temp(self, value):
        value = float(value)
        if value < 0 or value > 1:
            raise ValueError(
                "temperature must be greater than 0 and less than 1, it is currently "
                + str(value)
            )

        self._temp = value

    @property
    def top_p(self):
        return self._top_p

    @top_p.setter
    def top_p(self, value):
        value = float(value)
        if value < 0 or value > 1:
            raise ValueError(
                "top_p must be greater than 0 and less than 1, it is currently "
                + str(value)
            )
        self._top_p = value

    @property
    def max_tokens(self):
        return self._max_tokens

    @max_tokens.setter
    def max_tokens(self, value):
        value = int(value)
        if value < 15 or value > 4096:
            raise ValueError(
                "max_tokens must be greater than 15 and less than 4096, it is currently "
                + str(value)
            )
        self._max_tokens = value

    @property
    def presence_penalty(self):
        return self._presence_penalty

    @presence_penalty.setter
    def presence_penalty(self, value):
        if int(value) < 0:
            raise ValueError(
                "presence_penalty must be greater than 0, it is currently " + str(value)
            )
        self._presence_penalty = value

    @property
    def frequency_penalty(self):
        return self._frequency_penalty

    @frequency_penalty.setter
    def frequency_penalty(self, value):
        if int(value) < 0:
            raise ValueError(
                "frequency_penalty must be greater than 0, it is currently "
                + str(value)
            )
        self._frequency_penalty = value

    @property
    def best_of(self):
        return self._best_of

    @best_of.setter
    def best_of(self, value):
        value = int(value)
        if value < 1 or value > 3:
            raise ValueError(
                "best_of must be greater than 0 and ideally less than 3 to save tokens, it is currently "
                + str(value)
            )
        self._best_of = value

    @property
    def prompt_min_length(self):
        return self._prompt_min_length

    @prompt_min_length.setter
    def prompt_min_length(self, value):
        value = int(value)
        if value < 10 or value > 4096:
            raise ValueError(
                "prompt_min_length must be greater than 10 and less than 4096, it is currently "
                + str(value)
            )
        self._prompt_min_length = value

    async def valid_text_request(self, response):
        try:
            tokens_used = int(response["usage"]["total_tokens"])
            await self.usage_service.update_usage(tokens_used)
        except:
            raise ValueError(
                "The API returned an invalid response: "
                + str(response["error"]["message"])
            )

    async def send_embedding_request(self, text, custom_api_key=None):
        async with aiohttp.ClientSession() as session:
            payload = {
                "model": Models.EMBEDDINGS,
                "input": text,
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.openai_key if not custom_api_key else custom_api_key}",
            }
            async with session.post(
                "https://api.openai.com/v1/embeddings", json=payload, headers=headers
            ) as resp:
                response = await resp.json()

                try:
                    return response["data"][0]["embedding"]
                except Exception as e:
                    print(response)
                    traceback.print_exc()
                    return

    async def send_edit_request(self, instruction, input=None, temp_override=None, top_p_override=None, codex=False, custom_api_key=None):
        
        # Validate that  all the parameters are in a good state before we send the request
        if len(instruction) < self.prompt_min_length:
            raise ValueError(
                "Instruction must be greater than 8 characters, it is currently "
                + str(len(instruction))
            )
        

        print(f"The text about to be edited is [{input}] with instructions [{instruction}]")
        print(
            f"Overrides -> temp:{temp_override}, top_p:{top_p_override}"
        )
        
        async with aiohttp.ClientSession() as session:
            payload = {
                "model": Models.EDIT if codex is False else Models.CODE_EDIT,
                "input": "" if input is None else input,
                "instruction": instruction,
                "temperature": self.temp if temp_override is None else temp_override,
                "top_p": self.top_p if top_p_override is None else top_p_override
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.openai_key if not custom_api_key else custom_api_key}",
            }
            async with session.post(
                "https://api.openai.com/v1/edits", json=payload, headers=headers
            ) as resp:
                response = await resp.json()
                await self.valid_text_request(response)
                return response

    async def send_moderations_request(self, text):
        # Use aiohttp to send the above request:
        async with aiohttp.ClientSession() as session:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.openai_key}",
            }
            payload = {"input": text}
            async with session.post(
                "https://api.openai.com/v1/moderations",
                headers=headers,
                json=payload,
            ) as response:
                return await response.json()

    async def send_summary_request(self, prompt, custom_api_key=None):
        """
        Sends a summary request to the OpenAI API
        """
        summary_request_text = []
        summary_request_text.append(
            "The following is a conversation instruction set and a conversation"
            " between two people, a <username>, and GPTie. Firstly, determine the <username>'s name from the conversation history, then summarize the conversation. Do not summarize the instructions for GPTie, only the conversation. Summarize the conversation in a detailed fashion. If <username> mentioned their name, be sure to mention it in the summary. Pay close attention to things the <username> has told you, such as personal details."
        )
        summary_request_text.append(prompt + "\nDetailed summary of conversation: \n")

        summary_request_text = "".join(summary_request_text)

        tokens = self.usage_service.count_tokens(summary_request_text)

        async with aiohttp.ClientSession() as session:
            payload = {
                "model": Models.DAVINCI,
                "prompt": summary_request_text,
                "temperature": 0.5,
                "top_p": 1,
                "max_tokens": self.max_tokens - tokens,
                "presence_penalty": self.presence_penalty,
                "frequency_penalty": self.frequency_penalty,
                "best_of": self.best_of,
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.openai_key if not custom_api_key else custom_api_key}",
            }
            async with session.post(
                "https://api.openai.com/v1/completions", json=payload, headers=headers
            ) as resp:
                response = await resp.json()

                await self.valid_text_request(response)

                # print(response["choices"][0]["text"])

                return response

    async def send_request(
        self,
        prompt,
        tokens,
        temp_override=None,
        top_p_override=None,
        best_of_override=None,
        frequency_penalty_override=None,
        presence_penalty_override=None,
        max_tokens_override=None,
        custom_api_key=None,
    ) -> (
        dict,
        bool,
    ):  # The response, and a boolean indicating whether or not the context limit was reached.

        # Validate that  all the parameters are in a good state before we send the request
        if len(prompt) < self.prompt_min_length:
            raise ValueError(
                "Prompt must be greater than 8 characters, it is currently "
                + str(len(prompt))
            )

        print("The prompt about to be sent is " + prompt)
        print(
            f"Overrides -> temp:{temp_override}, top_p:{top_p_override} frequency:{frequency_penalty_override}, presence:{presence_penalty_override}"
        )

        async with aiohttp.ClientSession() as session:
            payload = {
                "model": self.model,
                "prompt": prompt,
                "temperature": self.temp if temp_override is None else temp_override,
                "top_p": self.top_p if top_p_override is None else top_p_override,
                "max_tokens": self.max_tokens - tokens
                if not max_tokens_override
                else max_tokens_override,
                "presence_penalty": self.presence_penalty
                if presence_penalty_override is None
                else presence_penalty_override,
                "frequency_penalty": self.frequency_penalty
                if frequency_penalty_override is None
                else frequency_penalty_override,
                "best_of": self.best_of if not best_of_override else best_of_override,
            }
            headers = {
                "Authorization": f"Bearer {self.openai_key if not custom_api_key else custom_api_key}"
            }
            async with session.post(
                "https://api.openai.com/v1/completions", json=payload, headers=headers
            ) as resp:
                response = await resp.json()
                # print(f"Payload -> {payload}")
                # Parse the total tokens used for this request and response pair from the response
                await self.valid_text_request(response)
                print(f"Response -> {response}")

                return response

    @staticmethod
    async def send_test_request(api_key):

        async with aiohttp.ClientSession() as session:
            payload = {
                "model": Models.CURIE,
                "prompt": "test.",
                "temperature": 1,
                "top_p": 1,
                "max_tokens": 10,
            }
            headers = {"Authorization": f"Bearer {api_key}"}
            async with session.post(
                "https://api.openai.com/v1/completions", json=payload, headers=headers
            ) as resp:
                response = await resp.json()
                try:
                    int(response["usage"]["total_tokens"])
                except:
                    raise ValueError(str(response["error"]["message"]))

                return response

    async def send_image_request(
        self, ctx, prompt, vary=None, custom_api_key=None
    ) -> tuple[File, list[Any]]:
        # Validate that  all the parameters are in a good state before we send the request
        words = len(prompt.split(" "))
        if words < 3 or words > 75:
            raise ValueError(
                "Prompt must be greater than 3 words and less than 75, it is currently "
                + str(words)
            )

        # print("The prompt about to be sent is " + prompt)
        await self.usage_service.update_usage_image(self.image_size)

        response = None

        if not vary:
            payload = {"prompt": prompt, "n": self.num_images, "size": self.image_size}
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.openai_key if not custom_api_key else custom_api_key}",
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.openai.com/v1/images/generations",
                    json=payload,
                    headers=headers,
                ) as resp:
                    response = await resp.json()
        else:
            async with aiohttp.ClientSession() as session:
                data = aiohttp.FormData()
                data.add_field("n", str(self.num_images))
                data.add_field("size", self.image_size)
                with open(vary, "rb") as f:
                    data.add_field(
                        "image", f, filename="file.png", content_type="image/png"
                    )

                    async with session.post(
                        "https://api.openai.com/v1/images/variations",
                        headers={
                            "Authorization": f"Bearer {self.openai_key if not custom_api_key else custom_api_key}",
                        },
                        data=data,
                    ) as resp:
                        response = await resp.json()

        # print(response)

        image_urls = []
        for result in response["data"]:
            image_urls.append(result["url"])

        # For each image url, open it as an image object using PIL
        images = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: [
                Image.open(requests.get(url, stream=True).raw) for url in image_urls
            ],
        )

        # Save all the images with a random name to self.IMAGE_SAVE_PATH
        image_names = [f"{uuid.uuid4()}.png" for _ in range(len(images))]
        for image, name in zip(images, image_names):
            await asyncio.get_running_loop().run_in_executor(
                None, image.save, f"{self.IMAGE_SAVE_PATH}/{name}"
            )

        # Update image_urls to include the local path to these new images
        image_urls = [f"{self.IMAGE_SAVE_PATH}/{name}" for name in image_names]

        widths, heights = zip(*(i.size for i in images))

        # Calculate the number of rows and columns needed for the grid
        num_rows = num_cols = int(math.ceil(math.sqrt(len(images))))

        # If there are only 2 images, set the number of rows to 1
        if len(images) == 2:
            num_rows = 1

        # Calculate the size of the combined image
        width = max(widths) * num_cols
        height = max(heights) * num_rows

        # Create a transparent image with the same size as the images
        transparent = await asyncio.get_running_loop().run_in_executor(
            None, lambda: Image.new("RGBA", (max(widths), max(heights)))
        )

        # Create a new image with the calculated size
        new_im = await asyncio.get_running_loop().run_in_executor(
            None, lambda: Image.new("RGBA", (width, height))
        )

        # Paste the images and transparent segments into the grid
        x_offset = y_offset = 0
        for im in images:
            await asyncio.get_running_loop().run_in_executor(
                None, new_im.paste, im, (x_offset, y_offset)
            )

            x_offset += im.size[0]
            if x_offset >= width:
                x_offset = 0
                y_offset += im.size[1]

        # Fill the remaining cells with transparent segments
        while y_offset < height:
            while x_offset < width:
                await asyncio.get_running_loop().run_in_executor(
                    None, new_im.paste, transparent, (x_offset, y_offset)
                )
                x_offset += transparent.size[0]

            x_offset = 0
            y_offset += transparent.size[1]

        # Save the new_im to a temporary file and return it as a discord.File
        temp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        await asyncio.get_running_loop().run_in_executor(
            None, new_im.save, temp_file.name
        )

        # Print the filesize of new_im, in mega bytes
        image_size = os.path.getsize(temp_file.name) / 1048576
        if ctx.guild is None:
            guild_file_limit = 8
        else:
            guild_file_limit = ctx.guild.filesize_limit / 1048576

        # If the image size is greater than 8MB, we can't return this to the user, so we will need to downscale the
        # image and try again
        safety_counter = 0
        while image_size > guild_file_limit:
            safety_counter += 1
            if safety_counter >= 3:
                break
            print(
                f"Image size is {image_size}MB, which is too large for this server {guild_file_limit}MB. Downscaling and trying again"
            )
            # We want to do this resizing asynchronously, so that it doesn't block the main thread during the resize.
            # We can use the asyncio.run_in_executor method to do this
            new_im = await asyncio.get_running_loop().run_in_executor(
                None,
                functools.partial(
                    new_im.resize, (int(new_im.width / 1.05), int(new_im.height / 1.05))
                ),
            )

            temp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            await asyncio.get_running_loop().run_in_executor(
                None, new_im.save, temp_file.name
            )
            image_size = os.path.getsize(temp_file.name) / 1000000
            print(f"New image size is {image_size}MB")

        return (discord.File(temp_file.name), image_urls)
