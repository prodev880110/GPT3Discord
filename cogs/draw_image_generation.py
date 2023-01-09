import asyncio
import os
import tempfile
import traceback
from io import BytesIO

import discord
from PIL import Image
from pycord.multicog import add_to_group


# We don't use the converser cog here because we want to be able to redo for the last images and text prompts at the same time
from models.env_service_model import EnvService
from models.user_model import RedoUser

redo_users = {}
users_to_interactions = {}
ALLOWED_GUILDS = EnvService.get_allowed_guilds()


class DrawDallEService(discord.Cog, name="DrawDallEService"):
    def __init__(
        self, bot, usage_service, model, message_queue, deletion_queue, converser_cog
    ):
        super().__init__()
        self.bot = bot
        self.usage_service = usage_service
        self.model = model
        self.message_queue = message_queue
        self.deletion_queue = deletion_queue
        self.converser_cog = converser_cog

        print("Draw service init")

    async def encapsulated_send(
        self,
        user_id,
        prompt,
        ctx,
        response_message=None,
        vary=None,
        draw_from_optimizer=None,
    ):
        await asyncio.sleep(0)
        # send the prompt to the model
        from_context = isinstance(ctx, discord.ApplicationContext)

        try:
            file, image_urls = await self.model.send_image_request(
                ctx, prompt, vary=vary if not draw_from_optimizer else None
            )
        except ValueError as e:
            (
                await ctx.channel.send(
                    f"Error: {e}. Please try again with a different prompt."
                )
                if not from_context
                else await ctx.respond(
                    f"Error: {e}. Please try again with a different prompt."
                )
            )
            return

        # Start building an embed to send to the user with the results of the image generation
        embed = discord.Embed(
            title="Image Generation Results"
            if not vary
            else "Image Generation Results (Varying)"
            if not draw_from_optimizer
            else "Image Generation Results (Drawing from Optimizer)",
            description=f"{prompt}",
            color=0xC730C7,
        )

        # Add the image file to the embed
        embed.set_image(url=f"attachment://{file.filename}")

        if not response_message:  # Original generation case
            # Start an interaction with the user, we also want to send data embed=embed, file=file, view=SaveView(image_urls, self, self.converser_cog)
            result_message = (
                await ctx.channel.send(
                    embed=embed,
                    file=file,
                )
                if not from_context
                else await ctx.respond(embed=embed, file=file)
            )

            await result_message.edit(
                view=SaveView(ctx, image_urls, self, self.converser_cog, result_message)
            )

            self.converser_cog.users_to_interactions[user_id] = []
            self.converser_cog.users_to_interactions[user_id].append(result_message.id)

            # Get the actual result message object
            if from_context:
                result_message = await ctx.fetch_message(result_message.id)

            redo_users[user_id] = RedoUser(prompt, ctx, ctx, result_message)

        else:
            if not vary:  # Editing case
                message = await response_message.edit(
                    embed=embed,
                    file=file,
                )
                await message.edit(
                    view=SaveView(ctx, image_urls, self, self.converser_cog, message)
                )
            else:  # Varying case
                if not draw_from_optimizer:
                    result_message = await response_message.edit_original_response(
                        content="Image variation completed!",
                        embed=embed,
                        file=file,
                    )
                    await result_message.edit(
                        view=SaveView(
                            ctx,
                            image_urls,
                            self,
                            self.converser_cog,
                            result_message,
                            True,
                        )
                    )

                else:
                    result_message = await response_message.edit_original_response(
                        content="I've drawn the optimized prompt!",
                        embed=embed,
                        file=file,
                    )
                    await result_message.edit(
                        view=SaveView(
                            ctx, image_urls, self, self.converser_cog, result_message
                        )
                    )

                    redo_users[user_id] = RedoUser(prompt, ctx, ctx, result_message)

                self.converser_cog.users_to_interactions[user_id].append(
                    response_message.id
                )
                self.converser_cog.users_to_interactions[user_id].append(
                    result_message.id
                )

    @add_to_group("dalle")
    @discord.slash_command(
        name="draw",
        description="Draw an image from a prompt",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.option(name="prompt", description="The prompt to draw from", required=True)
    async def draw(self, ctx: discord.ApplicationContext, prompt: str):
        await ctx.defer()

        user = ctx.user

        if user == self.bot.user:
            return

        try:
            asyncio.ensure_future(self.encapsulated_send(user.id, prompt, ctx))

        except Exception as e:
            print(e)
            traceback.print_exc()
            await ctx.respond("Something went wrong. Please try again later.")
            await ctx.send_followup(e)

    @add_to_group("system")
    @discord.slash_command(
        name="local-size",
        description="Get the size of the dall-e images folder that we have on the current system",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.guild_only()
    async def local_size(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        # Get the size of the dall-e images folder that we have on the current system.

        image_path = self.model.IMAGE_SAVE_PATH
        total_size = 0
        for dirpath, dirnames, filenames in os.walk(image_path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                total_size += os.path.getsize(fp)

        # Format the size to be in MB and send.
        total_size = total_size / 1000000
        await ctx.respond(f"The size of the local images folder is {total_size} MB.")

    @add_to_group("system")
    @discord.slash_command(
        name="clear-local",
        description="Clear the local dalleimages folder on system.",
        guild_ids=ALLOWED_GUILDS,
    )
    @discord.guild_only()
    async def clear_local(self, ctx):
        await ctx.defer()

        # Delete all the local images in the images folder.
        image_path = self.model.IMAGE_SAVE_PATH
        for dirpath, dirnames, filenames in os.walk(image_path):
            for f in filenames:
                try:
                    fp = os.path.join(dirpath, f)
                    os.remove(fp)
                except Exception as e:
                    print(e)

        await ctx.respond("Local images cleared.")


class SaveView(discord.ui.View):
    def __init__(
        self,
        ctx,
        image_urls,
        cog,
        converser_cog,
        message,
        no_retry=False,
        only_save=None,
    ):
        super().__init__(
            timeout=3600 if not only_save else None
        )  # 1 hour timeout for Retry, Save
        self.ctx = ctx
        self.image_urls = image_urls
        self.cog = cog
        self.no_retry = no_retry
        self.converser_cog = converser_cog
        self.message = message
        for x in range(1, len(image_urls) + 1):
            self.add_item(SaveButton(x, image_urls[x - 1]))
        if not only_save:
            if not no_retry:
                self.add_item(RedoButton(self.cog, converser_cog=self.converser_cog))
            for x in range(1, len(image_urls) + 1):
                self.add_item(
                    VaryButton(
                        x, image_urls[x - 1], self.cog, converser_cog=self.converser_cog
                    )
                )

    # On the timeout event, override it and we want to clear the items.
    async def on_timeout(self):
        # Save all the SaveButton items, then clear all the items, then add back the SaveButton items, then
        # update the message
        self.clear_items()

        # Create a new view with the same params as this one, but pass only_save=True
        new_view = SaveView(
            self.ctx,
            self.image_urls,
            self.cog,
            self.converser_cog,
            self.message,
            self.no_retry,
            only_save=True,
        )

        # Set the view of the message to the new view
        await self.ctx.edit(view=new_view)


class VaryButton(discord.ui.Button):
    def __init__(self, number, image_url, cog, converser_cog):
        super().__init__(style=discord.ButtonStyle.blurple, label="Vary " + str(number))
        self.number = number
        self.image_url = image_url
        self.cog = cog
        self.converser_cog = converser_cog

    async def callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        interaction_id = interaction.message.id

        if interaction_id not in self.converser_cog.users_to_interactions[user_id]:
            if len(self.converser_cog.users_to_interactions[user_id]) >= 2:
                interaction_id2 = interaction.id
                if (
                    interaction_id2
                    not in self.converser_cog.users_to_interactions[user_id]
                ):
                    await interaction.response.send_message(
                        content="You can not vary images in someone else's chain!",
                        ephemeral=True,
                    )
            else:
                await interaction.response.send_message(
                    content="You can only vary for images that you generated yourself!",
                    ephemeral=True,
                )
            return

        if user_id in redo_users:
            response_message = await interaction.response.send_message(
                content="Varying image number " + str(self.number) + "..."
            )
            self.converser_cog.users_to_interactions[user_id].append(
                response_message.message.id
            )
            self.converser_cog.users_to_interactions[user_id].append(
                response_message.id
            )
            prompt = redo_users[user_id].prompt

            asyncio.ensure_future(
                self.cog.encapsulated_send(
                    user_id,
                    prompt,
                    interaction.message,
                    response_message=response_message,
                    vary=self.image_url,
                )
            )


class SaveButton(discord.ui.Button["SaveView"]):
    def __init__(self, number: int, image_url: str):
        super().__init__(style=discord.ButtonStyle.gray, label="Save " + str(number))
        self.number = number
        self.image_url = image_url

    async def callback(self, interaction: discord.Interaction):
        # If the image url doesn't start with "http", then we need to read the file from the URI, and then send the
        # file to the user as an attachment.
        try:
            if not self.image_url.startswith("http"):
                with open(self.image_url, "rb") as f:
                    image = Image.open(BytesIO(f.read()))
                    temp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                    image.save(temp_file.name)

                    await interaction.response.send_message(
                        content="Here is your image for download (open original and save)",
                        file=discord.File(temp_file.name),
                        ephemeral=True,
                    )
            else:
                await interaction.response.send_message(
                    f"You can directly download this image from {self.image_url}",
                    ephemeral=True,
                )
        except Exception as e:
            await interaction.response.send_message(f"Error: {e}", ephemeral=True)
            traceback.print_exc()


class RedoButton(discord.ui.Button["SaveView"]):
    def __init__(self, cog, converser_cog):
        super().__init__(style=discord.ButtonStyle.danger, label="Retry")
        self.cog = cog
        self.converser_cog = converser_cog

    async def callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        interaction_id = interaction.message.id

        if interaction_id not in self.converser_cog.users_to_interactions[user_id]:
            await interaction.response.send_message(
                content="You can only retry for prompts that you generated yourself!",
                ephemeral=True,
            )
            return

        # We have passed the intial check of if the interaction belongs to the user
        if user_id in redo_users:
            # Get the message and the prompt and call encapsulated_send
            ctx = redo_users[user_id].ctx
            prompt = redo_users[user_id].prompt
            response_message = redo_users[user_id].response
            message = await interaction.response.send_message(
                f"Regenerating the image for your original prompt, check the original message.",
                ephemeral=True,
            )
            self.converser_cog.users_to_interactions[user_id].append(message.id)

            asyncio.ensure_future(
                self.cog.encapsulated_send(user_id, prompt, ctx, response_message)
            )
